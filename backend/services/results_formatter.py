"""
Data transformation utilities for converting database records to frontend format.
"""
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from uuid import UUID


def calculate_confidence_band(score: float) -> str:
    """
    Map confidence score (0.0-1.0) to confidence band.

    Args:
        score: Confidence score between 0.0 and 1.0

    Returns:
        "high", "medium", or "low"
    """
    if score >= 0.85:
        return "high"
    elif score >= 0.65:
        return "medium"
    else:
        return "low"


def derive_warnings(mapping: Dict[str, Any]) -> List[str]:
    """
    Generate warning array based on mapping properties.

    Args:
        mapping: Database mapping record

    Returns:
        List of warning strings
    """
    warnings = []

    # Add warning if needs review
    if mapping.get('needs_review', False):
        warnings.append('needs-review')

    # Add warning based on match type
    match_type = mapping.get('match_type', '')
    confidence = mapping.get('confidence_score', 0.0)

    # Add near-tie warning for medium confidence matches
    if 0.65 <= confidence < 0.85:
        warnings.append('near-tie')

    # Add low-confidence warning
    if confidence < 0.65:
        warnings.append('low-confidence')

    return warnings


def calculate_path_similarity(old_url: str, new_url: str) -> int:
    """
    Calculate similarity between URL paths using SequenceMatcher.

    Args:
        old_url: Old site URL.
        new_url: New site URL.

    Returns:
        Similarity score 0-100.
    """
    try:
        old_path = urlparse(old_url).path.strip('/')
        new_path = urlparse(new_url).path.strip('/')

        if old_path == new_path:
            return 100

        ratio = SequenceMatcher(None, old_path, new_path).ratio()
        return int(ratio * 100)
    except Exception:
        return 0


def calculate_title_similarity(old_title: str, new_title: str) -> int:
    """
    Calculate similarity between page titles using SequenceMatcher.

    Args:
        old_title: Title from old page.
        new_title: Title from new page.

    Returns:
        Similarity score 0-100.
    """
    if not old_title or not new_title:
        return 0

    if old_title == new_title:
        return 100

    ratio = SequenceMatcher(None, old_title.lower(), new_title.lower()).ratio()
    return int(ratio * 100)


def transform_mapping_for_frontend(
    db_record: Dict[str, Any],
    title_map: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Convert a single database mapping record to frontend format.

    Args:
        db_record: Database record from url_mappings table
        title_map: Optional URL-to-title lookup from webpage_embeddings

    Returns:
        Dictionary matching frontend RedirectMapping interface
    """
    # Convert confidence from 0.0-1.0 to 0-100
    confidence_score = db_record.get('confidence_score', 0.0)
    confidence_int = int(confidence_score * 100)

    # Calculate confidence band
    confidence_band = calculate_confidence_band(confidence_score)

    # Get warnings
    warnings = derive_warnings(db_record)

    # Calculate real similarity scores
    old_url = db_record['old_url']
    new_url = db_record['new_url']

    path_sim = calculate_path_similarity(old_url, new_url)
    content_sim = confidence_int  # confidence_score IS the content similarity

    title_sim = 0
    if title_map:
        old_title = title_map.get(old_url, '')
        new_title = title_map.get(new_url, '')
        title_sim = calculate_title_similarity(old_title, new_title)

    return {
        'id': str(db_record['id']),
        'oldUrl': old_url,
        'newUrl': new_url,
        'confidence': confidence_int,
        'confidenceBand': confidence_band,
        'matchScore': confidence_int,
        'matchType': db_record.get('match_type', 'semantic'),
        'approved': not db_record.get('needs_review', False),
        'warnings': warnings,
        'pathSimilarity': path_sim,
        'titleSimilarity': title_sim,
        'contentSimilarity': content_sim
    }


def calculate_stats(mappings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate aggregate statistics from mappings.

    Args:
        mappings: List of frontend-formatted mapping dictionaries

    Returns:
        Dictionary with total, high, medium, low counts and approval progress
    """
    total = len(mappings)

    if total == 0:
        return {
            'total': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'approved': 0,
            'approvalProgress': 0
        }

    high_count = len([m for m in mappings if m['confidenceBand'] == 'high'])
    medium_count = len([m for m in mappings if m['confidenceBand'] == 'medium'])
    low_count = len([m for m in mappings if m['confidenceBand'] == 'low'])
    approved_count = len([m for m in mappings if m['approved']])

    return {
        'total': total,
        'high': high_count,
        'medium': medium_count,
        'low': low_count,
        'approved': approved_count,
        'approvalProgress': round((approved_count / total) * 100)
    }


def _build_title_map(session_id: str) -> Dict[str, str]:
    """
    Build a URL-to-title lookup from webpage_embeddings for a session.

    Args:
        session_id: Migration session ID string.

    Returns:
        Dictionary mapping URL to page title.
    """
    try:
        from src.redirx.database import WebPageEmbeddingDB
        embedding_db = WebPageEmbeddingDB()
        embeddings = embedding_db.get_embeddings_by_session(
            UUID(session_id)
        )
        return {e['url']: e.get('title', '') for e in embeddings}
    except Exception:
        return {}


def format_results_response(
    db_mappings: List[Dict[str, Any]],
    session_metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Format complete results response for frontend.

    Args:
        db_mappings: List of database mapping records
        session_metadata: Optional session information

    Returns:
        Complete response with mappings, stats, and metadata
    """
    # Build title map from embeddings if we have a session
    # Skip for url_only pipelines (no embeddings exist)
    title_map = {}
    pipeline_type = session_metadata.get('pipeline_type', 'content') if session_metadata else 'content'
    if pipeline_type != 'url_only':
        if session_metadata and session_metadata.get('id'):
            title_map = _build_title_map(str(session_metadata['id']))
        elif db_mappings:
            session_id = db_mappings[0].get('session_id')
            if session_id:
                title_map = _build_title_map(str(session_id))

    # Transform all mappings
    frontend_mappings = [
        transform_mapping_for_frontend(m, title_map) for m in db_mappings
    ]

    # Calculate stats
    stats = calculate_stats(frontend_mappings)

    response = {
        'success': True,
        'mappings': frontend_mappings,
        'stats': stats
    }

    # Add session metadata if provided
    if session_metadata:
        response['session'] = {
            'id': str(session_metadata.get('id', '')),
            'status': session_metadata.get('status', 'unknown'),
            'created_at': session_metadata.get('created_at', ''),
            'user_id': session_metadata.get('user_id', ''),
            'pipeline_type': session_metadata.get('pipeline_type', 'content')
        }

    return response
