"""
Data transformation utilities for converting database records to frontend format.
"""
from typing import List, Dict, Any
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


def mock_similarity_scores() -> Dict[str, int]:
    """
    Temporary: return placeholder values for similarity scores.

    TODO: Calculate and store these in the pipeline stages.

    Returns:
        Dictionary with pathSimilarity, titleSimilarity, contentSimilarity
    """
    # For now, return reasonable defaults
    # In the future, calculate these from URL path analysis, title comparison, etc.
    return {
        'pathSimilarity': 75,
        'titleSimilarity': 80,
        'contentSimilarity': 85
    }


def transform_mapping_for_frontend(db_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a single database mapping record to frontend format.

    Args:
        db_record: Database record from url_mappings table

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

    # Get mock similarity scores (TODO: use real values)
    similarity = mock_similarity_scores()

    return {
        'id': str(db_record['id']),
        'oldUrl': db_record['old_url'],
        'newUrl': db_record['new_url'],
        'confidence': confidence_int,
        'confidenceBand': confidence_band,
        'matchScore': confidence_int,  # Same as confidence for now
        'approved': not db_record.get('needs_review', False),
        'warnings': warnings,
        'pathSimilarity': similarity['pathSimilarity'],
        'titleSimilarity': similarity['titleSimilarity'],
        'contentSimilarity': similarity['contentSimilarity']
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
    # Transform all mappings
    frontend_mappings = [transform_mapping_for_frontend(m) for m in db_mappings]

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
            'user_id': session_metadata.get('user_id', '')
        }

    return response
