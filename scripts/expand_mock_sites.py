#!/usr/bin/env python3
"""
Mock Site Expansion Tool for Redirx

Generates ~200-300 HTML pages per mock site with controlled similarity levels
and targeted edge cases for every pipeline stage. Uses only Python stdlib.
Deterministic output via seeded random.

Usage:
    python scripts/expand_mock_sites.py                    # Generate all pages
    python scripts/expand_mock_sites.py --dry-run          # Preview without writing
    python scripts/expand_mock_sites.py --clean            # Remove generated pages only
    python scripts/expand_mock_sites.py --old-dir PATH     # Override old site dir
    python scripts/expand_mock_sites.py --new-dir PATH     # Override new site dir
"""

import argparse
import csv
import hashlib
import os
import random
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ============================================================
# Constants
# ============================================================

MARKER = "<!-- generated-by-expand-mock-sites -->"
SEED = 42


def safe_str(path) -> str:
    """Convert path to a printable ASCII-safe string (handles Unicode LRM in paths)."""
    return str(path).encode("ascii", "replace").decode()

# Default directories (sibling to the Redirx-1 repo)
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_OLD_DIR = REPO_ROOT.parent / "mock-old-site"
DEFAULT_NEW_DIR = REPO_ROOT.parent / "mock-new-site"
MAPPING_MD_PATH = REPO_ROOT / "tests" / "mock_sites" / "SITE_MAPPING_EXPANDED.md"
MAPPING_CSV_PATH = REPO_ROOT / "tests" / "mock_sites" / "expected_mappings.csv"


# ============================================================
# Data Classes
# ============================================================

@dataclass
class PageSpec:
    """Specification for a generated HTML page."""
    path: str           # Relative path within the site dir, e.g. "team/john-doe.html"
    title: str          # Page <title> and <h1>
    description: str    # Meta description
    sections: list      # List of (heading, html_content) tuples
    css_classes: list = field(default_factory=list)


@dataclass
class MappingEntry:
    """A row in the mapping document."""
    old_url: str
    new_url: str
    match_type: str       # IDENTICAL, SIMILAR, MODERATE, RENAMED, RESTRUCTURED, ORPHANED, NEW, FILTERED
    similarity: str       # e.g. "100%", "85%", "0%", "N/A"
    stage: str            # Which pipeline stage handles it
    notes: str


# ============================================================
# Synonym Map & Text Transforms
# ============================================================

SYNONYM_MAP = {
    "solutions": "services",
    "comprehensive": "thorough",
    "innovative": "cutting-edge",
    "leverage": "utilize",
    "optimize": "enhance",
    "streamline": "simplify",
    "robust": "reliable",
    "scalable": "expandable",
    "expertise": "proficiency",
    "enterprise": "corporate",
    "implement": "deploy",
    "strategic": "tactical",
    "transform": "revolutionize",
    "deliver": "provide",
    "achieve": "accomplish",
    "growth": "expansion",
    "efficiency": "productivity",
    "performance": "capability",
    "infrastructure": "framework",
    "methodology": "approach",
    "integration": "incorporation",
    "platform": "system",
    "development": "creation",
    "management": "administration",
    "analysis": "assessment",
    "client": "customer",
    "partner": "collaborator",
    "industry": "sector",
    "technology": "tech",
    "digital": "electronic",
    "operations": "processes",
    "stakeholder": "participant",
    "initiative": "project",
    "strategy": "plan",
    "compliance": "adherence",
    "deployment": "rollout",
    "monitoring": "tracking",
    "automation": "mechanization",
    "governance": "oversight",
    "agile": "adaptive",
    "migration": "transition",
    "workflow": "process flow",
    "dashboard": "control panel",
    "analytics": "data insights",
    "cybersecurity": "information security",
    "resilience": "durability",
    "sustainability": "long-term viability",
    "collaboration": "teamwork",
    "engagement": "involvement",
    "alignment": "synchronization",
    "benchmark": "standard",
}

# Build reverse map
REVERSE_SYNONYM_MAP = {v: k for k, v in SYNONYM_MAP.items()}


def apply_synonyms(text: str, rng: random.Random, intensity: float = 0.3) -> str:
    """Replace words using synonym map at given intensity (0.0-1.0)."""
    words = text.split()
    result = []
    for word in words:
        lower = word.lower().strip(".,;:!?\"'()")
        prefix = word[:len(word) - len(word.lstrip(".,;:!?\"'()"))]
        suffix = word[len(word.rstrip(".,;:!?\"'()")):]
        core = word[len(prefix):len(word) - len(suffix)] if suffix else word[len(prefix):]

        if rng.random() < intensity and core.lower() in SYNONYM_MAP:
            replacement = SYNONYM_MAP[core.lower()]
            if core[0].isupper():
                replacement = replacement.capitalize()
            result.append(prefix + replacement + suffix)
        elif rng.random() < intensity and core.lower() in REVERSE_SYNONYM_MAP:
            replacement = REVERSE_SYNONYM_MAP[core.lower()]
            if core[0].isupper():
                replacement = replacement.capitalize()
            result.append(prefix + replacement + suffix)
        else:
            result.append(word)
    return " ".join(result)


def reorder_sentences(text: str, rng: random.Random) -> str:
    """Shuffle sentences within a paragraph."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 2:
        rng.shuffle(sentences)
    return " ".join(sentences)


def paraphrase_paragraph(text: str, rng: random.Random, intensity: float = 0.5) -> str:
    """Apply synonym replacement and optional sentence reordering."""
    text = apply_synonyms(text, rng, intensity)
    if rng.random() < 0.4:
        text = reorder_sentences(text, rng)
    return text


def transform_content(sections: list, rng: random.Random, similarity_target: float) -> list:
    """
    Transform sections to achieve a target similarity level.
    similarity_target: 1.0 = identical, 0.95 = minor changes, etc.
    Returns new list of (heading, content) tuples.
    """
    if similarity_target >= 1.0:
        return sections[:]

    new_sections = []
    intensity = 1.0 - similarity_target  # Higher intensity = more changes

    for heading, content in sections:
        new_heading = heading
        if similarity_target < 0.8 and rng.random() < 0.3:
            new_heading = apply_synonyms(heading, rng, 0.5)

        paragraphs = content.split("</p>")
        new_paragraphs = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            # Extract text between <p> tags
            match = re.match(r'(<p[^>]*>)(.*)', p, re.DOTALL)
            if match:
                tag, text = match.groups()
                if rng.random() < intensity * 1.5:
                    text = paraphrase_paragraph(text, rng, intensity)
                new_paragraphs.append(f"{tag}{text}</p>")
            else:
                new_paragraphs.append(p + "</p>" if p else "")

        new_content = "\n".join(new_paragraphs)

        # Section reordering for low similarity
        new_sections.append((new_heading, new_content))

    if similarity_target < 0.75 and len(new_sections) > 2:
        # Shuffle middle sections
        middle = new_sections[1:-1]
        rng.shuffle(middle)
        new_sections = [new_sections[0]] + middle + [new_sections[-1]]

    return new_sections


# ============================================================
# Content Pools
# ============================================================

FIRST_NAMES = [
    "Alexandra", "Benjamin", "Catherine", "Daniel", "Elena", "Franklin",
    "Grace", "Harrison", "Isabella", "James", "Katherine", "Lucas",
    "Meredith", "Nathan", "Olivia", "Patrick", "Quinn", "Rebecca",
    "Samuel", "Tanya", "Victor", "Wendy", "Xavier", "Yolanda",
    "Zachary",
]

LAST_NAMES = [
    "Anderson", "Brooks", "Campbell", "Davis", "Edwards", "Fleming",
    "Gomez", "Hayes", "Ibrahim", "Jenkins", "Kim", "Lawrence",
    "Mitchell", "Nguyen", "O'Brien", "Patel", "Reeves", "Singh",
    "Torres", "Vasquez", "Wallace", "Young", "Zhang", "Alvarez",
    "Bennett",
]

ROLES = [
    "Senior Software Engineer", "Principal Architect", "Engineering Manager",
    "Lead Data Scientist", "DevOps Lead", "UX Research Director",
    "Product Manager", "Solutions Consultant", "Cloud Architect",
    "Security Engineer", "QA Lead", "Technical Writer",
    "Frontend Lead", "Backend Engineer", "Mobile Developer",
    "Infrastructure Engineer", "Site Reliability Engineer",
    "Machine Learning Engineer", "Database Administrator",
    "Business Analyst", "Scrum Master", "Release Manager",
    "Performance Engineer", "API Developer", "Integration Specialist",
]

CITIES = [
    "Atlanta", "Boston", "Chicago", "Denver", "San Francisco",
    "New York", "Austin", "Seattle", "Portland", "Miami",
]

INDUSTRIES = [
    ("healthcare", "Healthcare", "Transforming patient care through technology, from electronic health records to telemedicine platforms and clinical decision support systems."),
    ("financial-services", "Financial Services", "Enabling digital banking, regulatory compliance, fraud detection, and algorithmic trading through modern technology platforms."),
    ("manufacturing", "Manufacturing", "Driving Industry 4.0 with IoT sensors, predictive maintenance, supply chain optimization, and smart factory solutions."),
    ("retail", "Retail & E-Commerce", "Building omnichannel experiences, inventory management systems, personalized recommendations, and payment processing platforms."),
    ("education", "Education & EdTech", "Creating learning management systems, virtual classrooms, student analytics, and adaptive learning platforms."),
    ("energy", "Energy & Utilities", "Modernizing grid management, enabling renewable energy integration, and building smart metering infrastructure."),
    ("logistics", "Logistics & Transportation", "Optimizing route planning, fleet management, warehouse automation, and real-time shipment tracking."),
    ("government", "Government & Public Sector", "Delivering citizen-facing digital services, modernizing legacy systems, and ensuring regulatory compliance."),
    ("media", "Media & Entertainment", "Building content delivery networks, streaming platforms, digital rights management, and audience analytics."),
    ("telecommunications", "Telecommunications", "Enabling 5G network management, customer experience platforms, and network function virtualization."),
    ("insurance", "Insurance", "Automating claims processing, underwriting analytics, risk assessment, and policyholder self-service portals."),
    ("real-estate", "Real Estate & PropTech", "Building property management platforms, virtual tour systems, and tenant experience applications."),
]

KB_CATEGORIES = [
    ("cloud-computing", "Cloud Computing"),
    ("data-engineering", "Data Engineering"),
    ("security", "Security Best Practices"),
    ("devops", "DevOps & CI/CD"),
    ("architecture", "System Architecture"),
]

KB_ARTICLES = [
    ("aws-migration-guide", "AWS Migration Guide", "cloud-computing",
     "A comprehensive guide to migrating enterprise workloads to Amazon Web Services, covering assessment, planning, execution, and optimization phases."),
    ("azure-best-practices", "Azure Best Practices", "cloud-computing",
     "Recommended practices for deploying and managing applications on Microsoft Azure, including cost management, security, and performance tuning."),
    ("gcp-data-analytics", "GCP Data Analytics Setup", "cloud-computing",
     "Setting up a data analytics pipeline on Google Cloud Platform using BigQuery, Dataflow, and Looker for enterprise reporting needs."),
    ("multi-cloud-strategy", "Multi-Cloud Strategy", "cloud-computing",
     "Developing a multi-cloud strategy that balances vendor independence, cost optimization, and operational complexity across providers."),
    ("kubernetes-production", "Kubernetes in Production", "cloud-computing",
     "Running Kubernetes clusters in production with high availability, automated scaling, monitoring, and disaster recovery considerations."),
    ("data-warehouse-design", "Data Warehouse Design Patterns", "data-engineering",
     "Modern data warehouse design patterns including star schemas, snowflake schemas, data vaults, and wide table approaches."),
    ("etl-pipeline-best-practices", "ETL Pipeline Best Practices", "data-engineering",
     "Building reliable ETL pipelines with error handling, idempotency, data validation, and monitoring for enterprise data integration."),
    ("real-time-streaming", "Real-Time Data Streaming", "data-engineering",
     "Implementing real-time data streaming with Apache Kafka, AWS Kinesis, or Azure Event Hubs for low-latency data processing."),
    ("data-quality-framework", "Data Quality Framework", "data-engineering",
     "Establishing a data quality framework with automated checks, data profiling, anomaly detection, and governance workflows."),
    ("data-lake-architecture", "Data Lake Architecture", "data-engineering",
     "Designing a modern data lake architecture with zone-based organization, metadata management, and query optimization strategies."),
    ("zero-trust-implementation", "Zero Trust Implementation", "security",
     "Implementing a zero trust security architecture with identity verification, micro-segmentation, and continuous authentication."),
    ("soc2-compliance", "SOC 2 Compliance Guide", "security",
     "Achieving SOC 2 Type II compliance for SaaS organizations, covering trust service criteria, evidence collection, and audit preparation."),
    ("incident-response-plan", "Incident Response Planning", "security",
     "Creating an effective incident response plan with defined roles, escalation procedures, communication templates, and post-incident reviews."),
    ("vulnerability-management", "Vulnerability Management Program", "security",
     "Building a vulnerability management program with scanning tools, prioritization frameworks, remediation workflows, and reporting dashboards."),
    ("api-security", "API Security Best Practices", "security",
     "Securing REST and GraphQL APIs with authentication, authorization, rate limiting, input validation, and threat modeling approaches."),
    ("ci-cd-pipeline-design", "CI/CD Pipeline Design", "devops",
     "Designing CI/CD pipelines with automated testing, quality gates, deployment strategies, and rollback capabilities."),
    ("infrastructure-as-code", "Infrastructure as Code Guide", "devops",
     "Managing infrastructure with Terraform, Pulumi, or CloudFormation for reproducible, version-controlled environments."),
    ("observability-stack", "Observability Stack Setup", "devops",
     "Building an observability stack with metrics, logs, and traces using Prometheus, Grafana, and OpenTelemetry."),
    ("gitops-workflow", "GitOps Workflow Guide", "devops",
     "Implementing GitOps workflows with ArgoCD or Flux for declarative, Git-driven Kubernetes deployments."),
    ("container-security", "Container Security", "devops",
     "Securing containerized applications with image scanning, runtime protection, network policies, and supply chain verification."),
    ("microservices-patterns", "Microservices Design Patterns", "architecture",
     "Essential microservices patterns including saga, circuit breaker, API gateway, service mesh, and event sourcing."),
    ("event-driven-architecture", "Event-Driven Architecture", "architecture",
     "Building event-driven systems with message brokers, event stores, CQRS, and eventual consistency patterns."),
    ("api-design-standards", "API Design Standards", "architecture",
     "Establishing API design standards covering REST conventions, versioning, pagination, error handling, and documentation."),
    ("domain-driven-design", "Domain-Driven Design Guide", "architecture",
     "Applying domain-driven design with bounded contexts, aggregates, value objects, and ubiquitous language in practice."),
    ("scalability-patterns", "Scalability Patterns", "architecture",
     "Horizontal and vertical scaling patterns including caching strategies, database sharding, read replicas, and CDN usage."),
]

EVENT_TITLES = [
    ("cloud-summit-2024", "Cloud Innovation Summit 2024", "2024-03-15",
     "Join industry leaders for a deep dive into cloud-native architectures, serverless computing, and multi-cloud strategies."),
    ("ai-workshop-series", "AI/ML Workshop Series", "2024-04-10",
     "Hands-on workshops covering machine learning model development, deployment, and monitoring for enterprise applications."),
    ("devops-conference", "DevOps Excellence Conference", "2024-05-22",
     "Explore the latest DevOps practices, CI/CD innovations, and platform engineering trends with expert speakers."),
    ("security-webinar-q1", "Quarterly Security Briefing Q1", "2024-01-30",
     "Review emerging threats, vulnerability trends, and security best practices for the enterprise environment."),
    ("data-engineering-day", "Data Engineering Day", "2024-06-14",
     "Full-day event covering modern data architectures, streaming platforms, and data governance frameworks."),
    ("product-launch-alpha", "Product Alpha Launch Event", "2024-02-28",
     "Be the first to see the new features in Product Alpha, including advanced analytics and integration capabilities."),
    ("digital-transformation-summit", "Digital Transformation Summit", "2024-07-18",
     "C-level discussions on driving organizational change through technology adoption and innovation."),
    ("kubernetes-masterclass", "Kubernetes Masterclass", "2024-08-05",
     "Advanced Kubernetes topics including service mesh, GitOps, multi-cluster management, and cost optimization."),
    ("women-in-tech", "Women in Tech Leadership Forum", "2024-09-12",
     "Celebrating and supporting women in technology leadership roles with panels, workshops, and networking."),
    ("annual-client-summit", "Annual Client Summit", "2024-10-24",
     "Our flagship event bringing together clients, partners, and technology experts for two days of innovation."),
    ("api-design-workshop", "API Design Workshop", "2024-04-25",
     "Practical workshop on REST API design principles, GraphQL adoption, and API governance strategies."),
    ("cloud-cost-optimization", "Cloud Cost Optimization Webinar", "2024-03-08",
     "Learn proven strategies for reducing cloud spending while maintaining performance and reliability."),
    ("microservices-migration", "Microservices Migration Workshop", "2024-06-28",
     "Step-by-step guidance for decomposing monolithic applications into microservices architectures."),
    ("cybersecurity-roundtable", "Cybersecurity Roundtable", "2024-11-15",
     "Executive roundtable discussing cybersecurity strategy, threat intelligence, and incident response."),
    ("year-end-review", "Technology Year in Review", "2024-12-10",
     "Looking back at the year's technology trends and forward to the innovations that will shape the coming year."),
]

INTEGRATION_PARTNERS = [
    ("salesforce", "Salesforce", "CRM integration enabling seamless data flow between your Salesforce instance and enterprise systems."),
    ("slack", "Slack", "Real-time notification and workflow integration with Slack for team communication and incident management."),
    ("jira", "Jira", "Project management integration connecting Jira with development pipelines and business workflows."),
    ("github", "GitHub", "Source code management and CI/CD integration with GitHub repositories and Actions."),
    ("aws", "Amazon Web Services", "Deep AWS integration for cloud infrastructure provisioning, monitoring, and cost management."),
    ("azure", "Microsoft Azure", "Azure ecosystem integration covering identity, compute, storage, and analytics services."),
    ("datadog", "Datadog", "Observability integration providing metrics, traces, and logs correlation for application monitoring."),
    ("snowflake", "Snowflake", "Data warehouse integration enabling analytics, reporting, and data sharing across the organization."),
    ("okta", "Okta", "Identity and access management integration for single sign-on and user provisioning."),
    ("twilio", "Twilio", "Communication integration for SMS, voice, and messaging capabilities in business applications."),
]

PORTFOLIO_PROJECTS = [
    ("financial-platform-modernization", "Financial Platform Modernization",
     "Modernized a legacy trading platform for a major financial services firm, migrating from on-premises infrastructure to a cloud-native architecture on AWS."),
    ("healthcare-patient-portal", "Healthcare Patient Portal",
     "Designed and built a patient-facing portal for a hospital network, enabling appointment scheduling, medical records access, and telemedicine consultations."),
    ("retail-inventory-system", "Retail Inventory Management System",
     "Developed a real-time inventory management system for a national retailer with 500+ locations, integrating POS, warehouse, and e-commerce channels."),
    ("government-citizen-services", "Government Citizen Services Platform",
     "Built a citizen services portal for a state government, processing permit applications, license renewals, and tax filings online."),
    ("edtech-learning-platform", "EdTech Learning Platform",
     "Created an adaptive learning platform for a K-12 education company, featuring personalized content recommendations and progress tracking."),
    ("manufacturing-iot-dashboard", "Manufacturing IoT Dashboard",
     "Built an IoT monitoring dashboard for a manufacturing plant, tracking equipment health, production metrics, and environmental conditions."),
    ("media-streaming-service", "Media Streaming Service",
     "Architected a video streaming platform handling millions of concurrent viewers with adaptive bitrate streaming and content recommendation engine."),
    ("logistics-route-optimization", "Logistics Route Optimization",
     "Developed an AI-powered route optimization system for a logistics company, reducing delivery times by 30% and fuel costs by 25%."),
    ("insurance-claims-automation", "Insurance Claims Automation",
     "Automated the claims processing pipeline for an insurance company, reducing processing time from weeks to hours with ML-powered document analysis."),
    ("telecom-network-management", "Telecom Network Management",
     "Built a network management platform for a telecommunications provider, monitoring thousands of cell towers and network nodes in real-time."),
    ("energy-grid-analytics", "Energy Grid Analytics Platform",
     "Created an analytics platform for a utility company to optimize energy distribution, predict demand, and integrate renewable sources."),
    ("real-estate-marketplace", "Real Estate Marketplace",
     "Developed a property marketplace with virtual tours, automated valuations, and mortgage pre-qualification for a real estate startup."),
    ("supply-chain-visibility", "Supply Chain Visibility Platform",
     "Built an end-to-end supply chain visibility platform tracking goods from manufacturer to consumer across global logistics networks."),
    ("banking-mobile-app", "Banking Mobile Application",
     "Designed and developed a mobile banking application with biometric authentication, real-time transactions, and personal financial management."),
    ("pharma-clinical-trials", "Pharmaceutical Clinical Trials Platform",
     "Created a clinical trials management platform for a pharmaceutical company, handling patient recruitment, data collection, and regulatory reporting."),
]

BLOG_TOPICS = [
    ("ai-enterprise-adoption", "AI Enterprise Adoption Strategies", "2024-02"),
    ("cloud-cost-management", "Cloud Cost Management Best Practices", "2024-03"),
    ("microservices-vs-monolith", "Microservices vs Monolith: When to Choose What", "2024-04"),
    ("devops-culture-shift", "Building a DevOps Culture", "2024-05"),
    ("data-mesh-introduction", "Introduction to Data Mesh Architecture", "2024-06"),
    ("kubernetes-security", "Kubernetes Security Hardening", "2024-07"),
    ("api-first-design", "API-First Design Principles", "2024-08"),
    ("observability-practices", "Modern Observability Practices", "2024-09"),
    ("green-computing", "Sustainable Computing Practices", "2024-10"),
    ("edge-computing-trends", "Edge Computing Trends for 2025", "2024-11"),
    ("platform-engineering", "Platform Engineering Fundamentals", "2024-12"),
    ("zero-trust-networks", "Zero Trust Network Architecture", "2024-02"),
    ("serverless-patterns", "Serverless Architecture Patterns", "2024-03"),
    ("tech-debt-management", "Managing Technical Debt", "2024-04"),
    ("ml-ops-pipeline", "Building an MLOps Pipeline", "2024-05"),
    ("event-driven-systems", "Event-Driven System Design", "2024-06"),
    ("container-orchestration", "Container Orchestration Strategies", "2024-07"),
    ("database-selection", "Choosing the Right Database", "2024-08"),
    ("frontend-performance", "Frontend Performance Optimization", "2024-09"),
    ("remote-team-management", "Managing Remote Engineering Teams", "2024-10"),
]

COMPANY_BIO_INTROS = [
    "With over {years} years of experience in {field}, {name} brings deep technical expertise and strategic thinking to every engagement.",
    "{name} is a seasoned {field} professional with {years} years of hands-on experience building enterprise-grade solutions.",
    "A recognized expert in {field}, {name} has spent {years} years helping organizations navigate complex technology challenges.",
    "{name} combines {years} years of {field} experience with a passion for mentoring teams and driving technical excellence.",
    "Since joining the technology industry {years} years ago, {name} has built a reputation for delivering innovative {field} solutions.",
]

COMPANY_BIO_DETAILS = [
    "Previously held senior roles at Fortune 500 companies including technology leadership and architecture positions.",
    "Holds multiple industry certifications and regularly speaks at conferences on topics related to enterprise technology.",
    "Known for a collaborative leadership style that empowers teams to deliver exceptional results under challenging deadlines.",
    "Passionate about continuous learning and staying at the forefront of emerging technologies and industry trends.",
    "Has successfully led cross-functional teams of 20+ engineers across multiple time zones and geographies.",
    "Specializes in translating complex technical concepts into actionable business strategies for executive stakeholders.",
    "Active contributor to open source projects and technical communities, with published articles in industry journals.",
    "Earned advanced degrees in computer science and maintains active certifications in cloud platforms and agile methodologies.",
]

TECH_DESCRIPTIONS = [
    "Our team designed and implemented a scalable microservices architecture that handles millions of transactions daily with 99.99% uptime.",
    "The solution leverages machine learning algorithms to predict demand patterns and optimize resource allocation in real time.",
    "We built a comprehensive CI/CD pipeline with automated testing, security scanning, and zero-downtime deployment capabilities.",
    "The platform features a responsive web application with offline capabilities, real-time synchronization, and progressive enhancement.",
    "Our architecture uses event-driven patterns with Apache Kafka for reliable message processing and data consistency across services.",
    "The system includes a robust API gateway with rate limiting, authentication, circuit breaking, and comprehensive request logging.",
    "We implemented a data lake architecture with bronze, silver, and gold zones for raw, cleaned, and curated data respectively.",
    "The infrastructure is fully managed through Terraform, with automated environment provisioning and configuration management.",
]


# ============================================================
# HTML Template Engine
# ============================================================

def render_old_page(spec: PageSpec, depth: int = 0) -> str:
    """Render an old site (TechCo Solutions) HTML page."""
    prefix = "../" * depth if depth > 0 else "/"
    # For root-level pages, use absolute paths
    if depth == 0:
        css_href = "/assets/styles.css"
        js_src = "/assets/main.js"
        nav_prefix = "/"
    else:
        css_href = f"{prefix}assets/styles.css"
        js_src = f"{prefix}assets/main.js"
        nav_prefix = prefix

    sections_html = ""
    for i, (heading, content) in enumerate(spec.sections):
        cls = spec.css_classes[i] if i < len(spec.css_classes) else "content-section"
        sections_html += f"""
            <section class="{cls}">
                <h2>{heading}</h2>
                {content}
            </section>
"""

    return f"""<!DOCTYPE html>
{MARKER}
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{spec.description}">
    <title>{spec.title} | TechCo Solutions</title>
    <link rel="stylesheet" href="{css_href}">
</head>
<body>
    <header>
        <div class="logo">
            <h2>TechCo Solutions</h2>
        </div>
        <nav>
            <ul>
                <li><a href="{nav_prefix}index.html">Home</a></li>
                <li><a href="{nav_prefix}about.html">About</a></li>
                <li><a href="{nav_prefix}services/index.html">Services</a></li>
                <li><a href="{nav_prefix}products/index.html">Products</a></li>
                <li><a href="{nav_prefix}blogs/index.html">Blog</a></li>
                <li><a href="{nav_prefix}contact.html">Contact</a></li>
            </ul>
        </nav>
    </header>

    <main>
        <article>
            <h1>{spec.title}</h1>
{sections_html}
        </article>
    </main>

    <footer>
        <p>&copy; 2024 TechCo Solutions. All rights reserved.</p>
        <nav>
            <a href="{nav_prefix}legal/privacy.html">Privacy Policy</a> |
            <a href="{nav_prefix}legal/terms.html">Terms of Service</a> |
            <a href="{nav_prefix}sitemap.html">Sitemap</a>
        </nav>
    </footer>

    <script src="{js_src}"></script>
</body>
</html>
"""


def render_new_page(spec: PageSpec, depth: int = 0) -> str:
    """Render a new site (TechCo) HTML page."""
    prefix = "../" * depth if depth > 0 else "/"
    if depth == 0:
        css_href = "/assets/styles.css"
        js_src = "/assets/app.js"
        nav_prefix = "/"
    else:
        css_href = f"{prefix}assets/styles.css"
        js_src = f"{prefix}assets/app.js"
        nav_prefix = prefix

    sections_html = ""
    for i, (heading, content) in enumerate(spec.sections):
        cls = spec.css_classes[i] if i < len(spec.css_classes) else "content-section"
        sections_html += f"""
            <section class="{cls}">
                <h2>{heading}</h2>
                {content}
            </section>
"""

    return f"""<!DOCTYPE html>
{MARKER}
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{spec.description}">
    <title>{spec.title} | TechCo</title>
    <link rel="stylesheet" href="{css_href}">
</head>
<body>
    <header>
        <div class="logo">
            <h2>TechCo</h2>
        </div>
        <nav>
            <ul>
                <li><a href="{nav_prefix}index.html">Home</a></li>
                <li><a href="{nav_prefix}about-us.html">About Us</a></li>
                <li><a href="{nav_prefix}solutions/index.html">Solutions</a></li>
                <li><a href="{nav_prefix}products/index.html">Products</a></li>
                <li><a href="{nav_prefix}news/index.html">News</a></li>
                <li><a href="{nav_prefix}get-in-touch.html">Contact</a></li>
            </ul>
        </nav>
    </header>

    <main>
        <article>
            <h1>{spec.title}</h1>
{sections_html}
        </article>
    </main>

    <footer>
        <p>&copy; 2024 TechCo. All rights reserved.</p>
        <nav>
            <a href="{nav_prefix}legal/privacy-policy.html">Privacy</a> |
            <a href="{nav_prefix}legal/terms-of-service.html">Terms</a> |
            <a href="{nav_prefix}site-map.html">Sitemap</a>
        </nav>
    </footer>

    <script src="{js_src}"></script>
</body>
</html>
"""


def render_minimal_html(title: str, body_content: str, site: str = "old") -> str:
    """Render a minimal HTML page for edge cases (small content)."""
    brand = "TechCo Solutions" if site == "old" else "TechCo"
    return f"""<!DOCTYPE html>
{MARKER}
<html lang="en">
<head><title>{title} | {brand}</title></head>
<body>
<main>{body_content}</main>
</body>
</html>
"""


def render_asset_placeholder(extension: str, content_hint: str) -> bytes:
    """Create a tiny placeholder file for asset types."""
    if extension == ".pdf":
        return b"%PDF-1.4 placeholder"
    elif extension == ".svg":
        return f'<svg xmlns="http://www.w3.org/2000/svg"><text>{content_hint}</text></svg>'.encode()
    elif extension == ".webp":
        return b"RIFF\x00\x00\x00\x00WEBP"  # Minimal RIFF/WEBP header
    else:
        return content_hint.encode()


# ============================================================
# Content Generators
# ============================================================

def generate_team_bios(rng: random.Random) -> tuple:
    """Generate 25 team member bio pages for both sites."""
    old_pages = []
    new_pages = []
    mappings = []

    similarity_bands = (
        [0.95] * 5 +   # 5 at ~95%
        [0.85] * 10 +  # 10 at ~85%
        [0.70] * 5 +   # 5 at ~70%
        [0.50] * 5     # 5 at ~50%
    )
    rng.shuffle(similarity_bands)

    for i in range(25):
        first = FIRST_NAMES[i]
        last = LAST_NAMES[i]
        slug = f"{first.lower()}-{last.lower()}"
        role = ROLES[i]
        years = rng.randint(5, 25)
        city = CITIES[i % len(CITIES)]
        field = rng.choice(["software engineering", "cloud architecture", "data engineering",
                            "solution architecture", "technology consulting"])

        bio_intro = rng.choice(COMPANY_BIO_INTROS).format(years=years, field=field, name=f"{first} {last}")
        bio_details = rng.sample(COMPANY_BIO_DETAILS, 3)
        tech_desc = rng.choice(TECH_DESCRIPTIONS)

        sections = [
            ("About", f"<p>{bio_intro}</p>\n<p>{bio_details[0]}</p>"),
            ("Experience", f"<p>{bio_details[1]}</p>\n<p>{tech_desc}</p>"),
            ("Background", f"<p>{bio_details[2]}</p>\n<p>Based in our {city} office, {first} works closely with clients across multiple industries to deliver technology solutions that drive measurable business outcomes.</p>"),
        ]

        old_spec = PageSpec(
            path=f"team/{slug}.html",
            title=f"{first} {last} - {role}",
            description=f"Meet {first} {last}, {role} at TechCo Solutions.",
            sections=sections,
            css_classes=["bio-intro", "experience", "background"],
        )
        old_pages.append(("old", old_spec, 1))

        similarity = similarity_bands[i]
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"our-team/{slug}.html",
            title=f"{first} {last} - {role}",
            description=f"Learn about {first} {last}, {role} at TechCo.",
            sections=new_sections,
            css_classes=["bio-intro", "experience", "background"],
        )
        new_pages.append(("new", new_spec, 1))

        sim_pct = int(similarity * 100)
        if sim_pct >= 90:
            match_type, stage = "SIMILAR", "PairingStage"
        elif sim_pct >= 80:
            match_type, stage = "RENAMED", "PairingStage"
        elif sim_pct >= 60:
            match_type, stage = "MODERATE", "PairingStage"
        else:
            match_type, stage = "MODERATE", "PairingStage"

        mappings.append(MappingEntry(
            old_url=f"/team/{slug}.html",
            new_url=f"/our-team/{slug}.html",
            match_type=match_type,
            similarity=f"{sim_pct}%",
            stage=stage,
            notes=f"Team bio: {first} {last}",
        ))

    return old_pages, new_pages, mappings


def generate_industry_pages(rng: random.Random) -> tuple:
    """Generate 12 industry vertical pages."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, name, description in INDUSTRIES:
        paragraphs = [
            f"<p>{description}</p>",
            f"<p>TechCo Solutions has deep expertise serving the {name.lower()} sector, with dedicated teams who understand the unique challenges and regulatory requirements of this industry.</p>",
            f"<p>Our track record includes successful projects with leading {name.lower()} organizations, delivering solutions that improve operational efficiency, enhance customer experiences, and drive revenue growth.</p>",
        ]

        sections = [
            (f"{name} Solutions", "\n".join(paragraphs)),
            ("Our Expertise", f"<p>We offer specialized consulting, custom development, and managed services tailored to the {name.lower()} industry. Our team includes professionals with direct industry experience who speak your language and understand your challenges.</p>"),
            ("Success Stories", f"<p>See how we have helped organizations in the {name.lower()} sector achieve their technology goals. From digital transformation to system modernization, our solutions deliver measurable business value.</p>"),
        ]

        old_spec = PageSpec(
            path=f"services/industries/{slug}.html",
            title=f"{name} Technology Solutions",
            description=f"TechCo Solutions {name.lower()} industry expertise and technology services.",
            sections=sections,
            css_classes=["industry-overview", "expertise", "success-stories"],
        )
        old_pages.append(("old", old_spec, 2))

        similarity = rng.choice([0.85, 0.88, 0.90, 0.82])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"solutions/industries/{slug}.html",
            title=f"{name} Technology Solutions",
            description=f"TechCo {name.lower()} industry expertise and technology solutions.",
            sections=new_sections,
            css_classes=["industry-overview", "expertise", "success-stories"],
        )
        new_pages.append(("new", new_spec, 2))

        mappings.append(MappingEntry(
            old_url=f"/services/industries/{slug}.html",
            new_url=f"/solutions/industries/{slug}.html",
            match_type="RENAMED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Industry: {name}",
        ))

    return old_pages, new_pages, mappings


def generate_location_pages(rng: random.Random) -> tuple:
    """Generate 8 office/location pages with shared boilerplate."""
    old_pages = []
    new_pages = []
    mappings = []

    # Shared boilerplate (tests near-duplicate detection)
    shared_boilerplate = (
        "<p>Our office provides a collaborative workspace designed for innovation and productivity. "
        "Equipped with modern technology, meeting rooms, and common areas, our facilities support "
        "both focused individual work and team collaboration.</p>"
        "<p>Visitors are welcome by appointment. Please contact us to schedule a meeting with our local team.</p>"
    )

    locations = [
        ("atlanta", "Atlanta", "100 Peachtree Street NW, Suite 2500, Atlanta, GA 30303", "(404) 555-0100"),
        ("boston", "Boston", "200 Clarendon Street, Suite 4800, Boston, MA 02116", "(617) 555-0200"),
        ("chicago", "Chicago", "233 South Wacker Drive, Suite 6200, Chicago, IL 60606", "(312) 555-0300"),
        ("denver", "Denver", "1700 Lincoln Street, Suite 3800, Denver, CO 80203", "(303) 555-0400"),
        ("san-francisco", "San Francisco", "555 California Street, Suite 4100, San Francisco, CA 94104", "(415) 555-0500"),
        ("new-york", "New York", "One World Trade Center, Suite 8500, New York, NY 10007", "(212) 555-0600"),
        ("austin", "Austin", "500 West 2nd Street, Suite 1900, Austin, TX 78701", "(512) 555-0700"),
        ("seattle", "Seattle", "1201 Third Avenue, Suite 4600, Seattle, WA 98101", "(206) 555-0800"),
    ]

    for slug, city, address, phone in locations:
        sections = [
            (f"{city} Office", f"<p>Our {city} office serves clients throughout the region with a team of experienced technology professionals specializing in consulting, development, and support services.</p>\n<p><strong>Address:</strong> {address}</p>\n<p><strong>Phone:</strong> {phone}</p>"),
            ("Facilities", shared_boilerplate),
            ("Local Team", f"<p>The {city} team includes specialists in cloud architecture, data engineering, software development, and project management. Together they bring decades of combined experience serving organizations in the region.</p>"),
        ]

        old_spec = PageSpec(
            path=f"locations/{slug}.html",
            title=f"{city} Office",
            description=f"TechCo Solutions {city} office location and contact information.",
            sections=sections,
            css_classes=["location-info", "facilities", "team"],
        )
        old_pages.append(("old", old_spec, 1))

        similarity = rng.choice([0.88, 0.90, 0.92])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"offices/{slug}.html",
            title=f"{city} Office",
            description=f"TechCo {city} office location and team information.",
            sections=new_sections,
            css_classes=["location-info", "facilities", "team"],
        )
        new_pages.append(("new", new_spec, 1))

        mappings.append(MappingEntry(
            old_url=f"/locations/{slug}.html",
            new_url=f"/offices/{slug}.html",
            match_type="RENAMED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Office: {city} (shared boilerplate)",
        ))

    return old_pages, new_pages, mappings


def generate_kb_articles(rng: random.Random) -> tuple:
    """Generate 25 knowledge base articles with deep nesting."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, title, category, description in KB_ARTICLES:
        cat_slug, cat_name = next((s, n) for s, n in KB_CATEGORIES if s == category)

        sections = [
            ("Overview", f"<p>{description}</p>\n<p>This guide is designed for technology leaders, architects, and engineers who need practical guidance on implementing {title.lower()} in enterprise environments.</p>"),
            ("Key Concepts", f"<p>Understanding the foundational concepts is essential before diving into implementation details. This section covers the core principles, terminology, and frameworks that underpin {title.lower()}.</p>\n<p>We recommend reviewing these concepts thoroughly before proceeding to the implementation sections below.</p>"),
            ("Implementation Guide", f"<p>Follow this step-by-step implementation guide to apply {title.lower()} in your organization. Each step includes detailed instructions, code examples where applicable, and troubleshooting tips for common issues.</p>\n<p>Our implementation approach is based on real-world experience across hundreds of client engagements and reflects current industry best practices.</p>"),
            ("Best Practices", f"<p>These best practices have been distilled from our experience implementing {title.lower()} across diverse organizations and environments. Following these guidelines will help you avoid common pitfalls and achieve optimal results.</p>"),
        ]

        old_spec = PageSpec(
            path=f"resources/kb/{cat_slug}/{slug}.html",
            title=title,
            description=f"TechCo Solutions knowledge base: {title}",
            sections=sections,
            css_classes=["overview", "concepts", "implementation", "best-practices"],
        )
        old_pages.append(("old", old_spec, 3))

        similarity = rng.choice([0.82, 0.85, 0.88, 0.90, 0.92])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"resource-center/knowledge-base/{cat_slug}/{slug}.html",
            title=title,
            description=f"TechCo knowledge base: {title}",
            sections=new_sections,
            css_classes=["overview", "concepts", "implementation", "best-practices"],
        )
        new_pages.append(("new", new_spec, 3))

        mappings.append(MappingEntry(
            old_url=f"/resources/kb/{cat_slug}/{slug}.html",
            new_url=f"/resource-center/knowledge-base/{cat_slug}/{slug}.html",
            match_type="RESTRUCTURED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"KB article: {title} (deep nesting)",
        ))

    return old_pages, new_pages, mappings


def generate_events(rng: random.Random) -> tuple:
    """Generate 15 event/webinar pages."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, title, date_str, description in EVENT_TITLES:
        sections = [
            ("Event Details", f"<p>{description}</p>\n<p><strong>Date:</strong> {date_str}</p>\n<p><strong>Format:</strong> {'Virtual' if rng.random() > 0.4 else 'In-person'} event</p>"),
            ("What You Will Learn", f"<p>Attendees will gain practical insights into current trends, proven strategies, and actionable frameworks that can be applied immediately in their organizations.</p>\n<p>Sessions are led by industry experts and TechCo thought leaders with extensive real-world experience.</p>"),
            ("Who Should Attend", f"<p>This event is designed for technology leaders, architects, developers, and business stakeholders who want to stay current with the latest developments and best practices.</p>"),
        ]

        old_spec = PageSpec(
            path=f"resources/events/{slug}.html",
            title=title,
            description=f"TechCo Solutions event: {title}",
            sections=sections,
            css_classes=["event-details", "agenda", "audience"],
        )
        old_pages.append(("old", old_spec, 2))

        similarity = rng.choice([0.80, 0.85, 0.88, 0.92])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"resource-center/events/{slug}.html",
            title=title,
            description=f"TechCo event: {title}",
            sections=new_sections,
            css_classes=["event-details", "agenda", "audience"],
        )
        new_pages.append(("new", new_spec, 2))

        mappings.append(MappingEntry(
            old_url=f"/resources/events/{slug}.html",
            new_url=f"/resource-center/events/{slug}.html",
            match_type="RESTRUCTURED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Event: {title}",
        ))

    return old_pages, new_pages, mappings


def generate_integrations(rng: random.Random) -> tuple:
    """Generate 10 integration/partner pages."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, name, description in INTEGRATION_PARTNERS:
        sections = [
            (f"{name} Integration", f"<p>{description}</p>\n<p>Our certified integration specialists ensure seamless connectivity between {name} and your existing technology stack.</p>"),
            ("Key Features", f"<p>Pre-built connectors and APIs for rapid integration deployment. Bidirectional data synchronization with configurable mapping rules. Real-time event processing and webhook support for immediate updates. Comprehensive error handling and retry logic for enterprise reliability.</p>"),
            ("Getting Started", f"<p>Contact our integration team to discuss your {name} integration requirements. We offer both standard connectors and custom integration development to meet your specific needs.</p>"),
        ]

        old_spec = PageSpec(
            path=f"partners/integrations/{slug}.html",
            title=f"{name} Integration",
            description=f"TechCo Solutions {name} integration capabilities.",
            sections=sections,
            css_classes=["integration-overview", "features", "getting-started"],
        )
        old_pages.append(("old", old_spec, 2))

        similarity = rng.choice([0.82, 0.85, 0.88])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"products/integrations/{slug}.html",
            title=f"{name} Integration",
            description=f"TechCo {name} integration capabilities.",
            sections=new_sections,
            css_classes=["integration-overview", "features", "getting-started"],
        )
        new_pages.append(("new", new_spec, 2))

        mappings.append(MappingEntry(
            old_url=f"/partners/integrations/{slug}.html",
            new_url=f"/products/integrations/{slug}.html",
            match_type="RESTRUCTURED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Integration: {name} (partners/ -> products/integrations/)",
        ))

    return old_pages, new_pages, mappings


def generate_portfolio(rng: random.Random) -> tuple:
    """Generate 15 portfolio/case study pages."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, title, description in PORTFOLIO_PROJECTS:
        tech_detail = rng.choice(TECH_DESCRIPTIONS)
        sections = [
            ("Project Overview", f"<p>{description}</p>"),
            ("Technical Approach", f"<p>{tech_detail}</p>\n<p>The project was delivered using agile methodology with two-week sprints, continuous integration, and regular stakeholder demonstrations.</p>"),
            ("Results", f"<p>The solution delivered measurable business outcomes including improved operational efficiency, reduced costs, and enhanced user satisfaction. Our client reported significant ROI within the first year of deployment.</p>"),
            ("Technologies Used", f"<p>This project leveraged modern technologies including cloud platforms, containerized microservices, automated CI/CD pipelines, and comprehensive monitoring and observability tools.</p>"),
        ]

        old_spec = PageSpec(
            path=f"case-studies/projects/{slug}.html",
            title=title,
            description=f"TechCo Solutions case study: {title}",
            sections=sections,
            css_classes=["overview", "approach", "results", "technologies"],
        )
        old_pages.append(("old", old_spec, 2))

        similarity = rng.choice([0.80, 0.85, 0.88, 0.90])
        new_sections = transform_content(sections, rng, similarity)

        new_spec = PageSpec(
            path=f"success-stories/projects/{slug}.html",
            title=title,
            description=f"TechCo success story: {title}",
            sections=new_sections,
            css_classes=["overview", "approach", "results", "technologies"],
        )
        new_pages.append(("new", new_spec, 2))

        mappings.append(MappingEntry(
            old_url=f"/case-studies/projects/{slug}.html",
            new_url=f"/success-stories/projects/{slug}.html",
            match_type="RENAMED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Portfolio: {title}",
        ))

    return old_pages, new_pages, mappings


def generate_blog_posts(rng: random.Random) -> tuple:
    """Generate 20 blog posts (old site only) for BlogPruneStage testing."""
    old_pages = []
    new_pages = []
    mappings = []

    for slug, title, date_prefix in BLOG_TOPICS:
        sections = [
            ("Introduction", f"<p>In this article, we explore {title.lower()} and its implications for enterprise technology strategy. Drawing from our experience working with organizations across industries, we share practical insights and actionable recommendations.</p>"),
            ("Key Insights", f"<p>The landscape of {title.lower()} is evolving rapidly. Organizations that stay ahead of these trends position themselves for competitive advantage and operational excellence. We have observed several patterns that distinguish successful implementations from those that struggle.</p>"),
            ("Recommendations", f"<p>Based on our analysis and hands-on experience, we recommend a phased approach to {title.lower()} that balances innovation with risk management. Start with a pilot project, measure results, and scale based on proven outcomes.</p>"),
            ("Conclusion", f"<p>{title} represents a significant opportunity for organizations willing to invest strategically. The key is to start with clear objectives, build the right team, and iterate based on real-world feedback.</p>"),
        ]

        old_spec = PageSpec(
            path=f"blogs/{date_prefix}-{slug}.html",
            title=title,
            description=f"TechCo Solutions blog: {title}",
            sections=sections,
            css_classes=["article-meta", "intro", "insights", "conclusion"],
        )
        old_pages.append(("old", old_spec, 1))

        mappings.append(MappingEntry(
            old_url=f"/blogs/{date_prefix}-{slug}.html",
            new_url="-",
            match_type="FILTERED",
            similarity="N/A",
            stage="BlogPruneStage",
            notes=f"Blog post filtered by date pattern ({date_prefix})",
        ))

    return old_pages, new_pages, mappings


def generate_section_indexes(rng: random.Random) -> tuple:
    """Generate 8 section index/landing pages."""
    old_pages = []
    new_pages = []
    mappings = []

    index_pages = [
        ("team/index.html", "our-team/index.html", "Our Team", "Meet the TechCo team"),
        ("locations/index.html", "offices/index.html", "Our Offices", "TechCo office locations"),
        ("resources/kb/index.html", "resource-center/knowledge-base/index.html", "Knowledge Base", "Browse our knowledge base"),
        ("resources/events/index.html", "resource-center/events/index.html", "Events & Webinars", "Upcoming events"),
        ("partners/index.html", "products/partners/index.html", "Partners", "Browse our partners"),
        ("partners/integrations/index.html", "products/integrations/index.html", "Integration Directory", "All available integrations"),
        ("case-studies/projects/index.html", "success-stories/projects/index.html", "Project Portfolio", "Browse our projects"),
        ("services/industries/index.html", "solutions/industries/index.html", "Industry Solutions", "Solutions by industry"),
    ]

    for old_path, new_path, title, desc in index_pages:
        sections = [
            ("Overview", f"<p>Browse our {title.lower()} section to find the information you need. Use the links below to navigate to specific pages.</p>"),
            ("Featured", f"<p>Explore our featured content in the {title.lower()} category. We regularly update this section with new additions and improvements.</p>"),
        ]

        depth = old_path.count("/") - 1
        old_spec = PageSpec(
            path=old_path,
            title=title,
            description=f"TechCo Solutions - {desc}",
            sections=sections,
            css_classes=["overview", "featured"],
        )
        old_pages.append(("old", old_spec, depth))

        similarity = rng.choice([0.78, 0.82, 0.85])
        new_sections = transform_content(sections, rng, similarity)

        new_depth = new_path.count("/") - 1
        new_spec = PageSpec(
            path=new_path,
            title=title,
            description=f"TechCo - {desc}",
            sections=new_sections,
            css_classes=["overview", "featured"],
        )
        new_pages.append(("new", new_spec, new_depth))

        mappings.append(MappingEntry(
            old_url=f"/{old_path}",
            new_url=f"/{new_path}",
            match_type="RESTRUCTURED",
            similarity=f"{int(similarity * 100)}%",
            stage="PairingStage",
            notes=f"Section index: {title}",
        ))

    return old_pages, new_pages, mappings


def generate_edge_cases(rng: random.Random) -> tuple:
    """Generate targeted edge case pages for each pipeline stage."""
    old_pages = []
    new_pages = []
    mappings = []

    # --- 1. Exact URL path matches (5 pairs) for ExactUrlMatchStage ---
    exact_url_pages = [
        ("solutions/tools/terraform.html", "Terraform Automation", "Infrastructure as code automation with Terraform for reproducible cloud environment provisioning."),
        ("solutions/tools/ansible.html", "Ansible Automation", "Configuration management and application deployment automation with Ansible playbooks."),
        ("solutions/tools/docker.html", "Docker Containerization", "Container platform services with Docker for consistent application packaging and deployment."),
        ("solutions/tools/grafana.html", "Grafana Dashboards", "Observability dashboards with Grafana for real-time metrics visualization and alerting."),
        ("solutions/tools/vault.html", "HashiCorp Vault", "Secrets management with HashiCorp Vault for secure credential storage and access control."),
    ]

    for path, title, desc in exact_url_pages:
        sections = [
            ("Overview", f"<p>{desc}</p>"),
            ("Features", "<p>Enterprise-grade integration with automated data synchronization, error handling, and comprehensive audit logging. Supports both real-time and batch processing modes.</p>"),
        ]
        depth = path.count("/")

        old_spec = PageSpec(path=path, title=title, description=desc,
                           sections=sections, css_classes=["overview", "features"])
        old_pages.append(("old", old_spec, depth))

        # Different content but same URL path
        similarity = rng.choice([0.75, 0.80])
        new_sections = transform_content(sections, rng, similarity)
        new_spec = PageSpec(path=path, title=title, description=desc,
                           sections=new_sections, css_classes=["overview", "features"])
        new_pages.append(("new", new_spec, depth))

        mappings.append(MappingEntry(
            old_url=f"/{path}", new_url=f"/{path}",
            match_type="SIMILAR", similarity=f"{int(similarity * 100)}%",
            stage="ExactUrlMatchStage",
            notes="Exact URL path match (pre-scrape)",
        ))

    # --- 2. Byte-identical HTML pairs (8 pairs) for HtmlPruneStage ---
    identical_pages = [
        ("resources/policies/data-retention.html", "resource-center/policies/data-retention.html", "Data Retention Policy"),
        ("resources/policies/acceptable-use.html", "resource-center/policies/acceptable-use.html", "Acceptable Use Policy"),
        ("resources/policies/security-standards.html", "resource-center/policies/security-standards.html", "Security Standards"),
        ("resources/guides/onboarding.html", "resource-center/guides/onboarding.html", "Client Onboarding Guide"),
        ("resources/guides/api-quickstart.html", "resource-center/guides/api-quickstart.html", "API Quickstart Guide"),
        ("resources/guides/migration-checklist.html", "resource-center/guides/migration-checklist.html", "Migration Checklist"),
        ("resources/guides/deployment-runbook.html", "resource-center/guides/deployment-runbook.html", "Deployment Runbook"),
        ("resources/guides/testing-strategy.html", "resource-center/guides/testing-strategy.html", "Testing Strategy Guide"),
    ]

    for old_path, new_path, title in identical_pages:
        desc = f"TechCo reference document: {title}"
        content_paragraphs = [
            f"<p>This document outlines the {title.lower()} for TechCo and its clients. It provides detailed guidelines, procedures, and requirements that apply to all stakeholders.</p>",
            f"<p>All team members and partners are expected to familiarize themselves with the contents of this document and adhere to the guidelines described herein. Questions should be directed to the appropriate department lead.</p>",
            f"<p>This document is reviewed and updated quarterly to ensure alignment with current best practices, regulatory requirements, and organizational policies.</p>",
        ]

        sections = [
            ("Purpose", content_paragraphs[0]),
            ("Scope", content_paragraphs[1]),
            ("Review Schedule", content_paragraphs[2]),
        ]

        old_depth = old_path.count("/")
        # IMPORTANT: For byte-identical, we render the SAME HTML for both
        # We use the old_page renderer and store the exact same output for both
        old_spec = PageSpec(path=old_path, title=title, description=desc,
                           sections=sections, css_classes=["purpose", "scope", "review"])
        old_pages.append(("old", old_spec, old_depth))

        # Mark as "identical_html" so the writer knows to copy the old HTML verbatim
        new_spec = PageSpec(path=new_path, title=title, description=desc,
                           sections=sections, css_classes=["purpose", "scope", "review"])
        new_pages.append(("identical_html", new_spec, old_depth))

        mappings.append(MappingEntry(
            old_url=f"/{old_path}", new_url=f"/{new_path}",
            match_type="IDENTICAL", similarity="100%",
            stage="HtmlPruneStage",
            notes="Byte-identical HTML (different URL)",
        ))

    # --- 3. Asset placeholders (6) for UrlPruneStage ---
    asset_files = [
        ("assets/report-2024.pdf", "assets/annual-report.pdf", "PDF report"),
        ("assets/architecture-diagram.svg", "assets/system-diagram.svg", "SVG diagram"),
        ("assets/office-photo.webp", "assets/team-photo.webp", "WebP image"),
        ("assets/product-datasheet.pdf", "assets/datasheet.pdf", "PDF datasheet"),
        ("assets/logo-icon.svg", "assets/brand-icon.svg", "SVG icon"),
        ("assets/banner-hero.webp", "assets/hero-image.webp", "WebP banner"),
    ]

    for old_path, new_path, hint in asset_files:
        ext = os.path.splitext(old_path)[1]
        old_pages.append(("asset", old_path, render_asset_placeholder(ext, hint)))
        new_pages.append(("asset", new_path, render_asset_placeholder(ext, hint)))

        mappings.append(MappingEntry(
            old_url=f"/{old_path}", new_url=f"/{new_path}",
            match_type="FILTERED", similarity="N/A",
            stage="UrlPruneStage",
            notes=f"Asset file ({ext}) should be filtered",
        ))

    # --- 4. Boilerplate-heavy pages (2 pairs) ---
    for i, topic in enumerate(["Cookie Policy", "Disclaimer"]):
        slug = topic.lower().replace(" ", "-")
        # Tiny <main> content, lots of nav/footer
        sections = [
            ("", f"<p>{topic} content.</p>"),
        ]
        old_spec = PageSpec(
            path=f"legal/{slug}.html", title=topic,
            description=f"{topic} for TechCo Solutions",
            sections=sections, css_classes=["legal-text"],
        )
        old_pages.append(("old", old_spec, 1))

        new_spec = PageSpec(
            path=f"legal/{slug}.html", title=topic,
            description=f"{topic} for TechCo",
            sections=sections, css_classes=["legal-text"],
        )
        new_pages.append(("new", new_spec, 1))

        mappings.append(MappingEntry(
            old_url=f"/legal/{slug}.html", new_url=f"/legal/{slug}.html",
            match_type="SIMILAR", similarity="90%",
            stage="ExactUrlMatchStage",
            notes="Boilerplate-heavy page (tiny <main>)",
        ))

    # --- 5. Minimal content pages (2 pairs) - below MIN_HTML_LENGTH threshold ---
    for i, label in enumerate(["placeholder-a", "placeholder-b"]):
        # <50 bytes in <main> -> should be skipped by HtmlPruneStage/EmbedStage
        old_html = render_minimal_html(f"Placeholder {i+1}", "<p>TBD</p>", "old")
        new_html = render_minimal_html(f"Placeholder {i+1}", "<p>TBD</p>", "new")
        old_pages.append(("raw_html", f"misc/{label}.html", old_html))
        new_pages.append(("raw_html", f"misc/{label}.html", new_html))

        mappings.append(MappingEntry(
            old_url=f"/misc/{label}.html", new_url=f"/misc/{label}.html",
            match_type="SIMILAR", similarity="N/A",
            stage="ExactUrlMatchStage",
            notes="Minimal content (<100 bytes HTML) - skipped by embedding",
        ))

    # --- 6. Same text, different HTML structure (3 pairs) ---
    struct_pages = [
        ("resources/tech-overview.html", "resource-center/tech-overview.html", "Technology Overview"),
        ("resources/methodology.html", "resource-center/methodology.html", "Our Methodology"),
        ("resources/approach.html", "resource-center/approach.html", "Our Approach"),
    ]

    for old_path, new_path, title in struct_pages:
        text_content = f"Our {title.lower()} combines proven strategies with innovative techniques. We deliver consistent results across diverse environments and industries. Every engagement benefits from our extensive experience and rigorous quality standards."

        # Old: content in <div> tags
        old_sections = [
            ("Overview", f"<div class='content'><p>{text_content}</p></div>"),
        ]
        old_spec = PageSpec(path=old_path, title=title,
                           description=f"TechCo Solutions {title.lower()}",
                           sections=old_sections, css_classes=["overview"])
        old_pages.append(("old", old_spec, 1))

        # New: same text in <section> + <aside> tags
        new_sections = [
            ("Overview", f"<section class='main-content'><p>{text_content}</p></section><aside><p>For more details, contact our team.</p></aside>"),
        ]
        new_spec = PageSpec(path=new_path, title=title,
                           description=f"TechCo {title.lower()}",
                           sections=new_sections, css_classes=["overview"])
        new_pages.append(("new", new_spec, 1))

        mappings.append(MappingEntry(
            old_url=f"/{old_path}", new_url=f"/{new_path}",
            match_type="RESTRUCTURED", similarity="85%",
            stage="PairingStage",
            notes="Same text, different HTML tags (hash differs, embeddings match)",
        ))

    # --- 7. Deep nesting (3 pairs) ---
    deep_pages = [
        ("services/enterprise/healthcare/compliance/hipaa.html",
         "solutions/enterprise/healthcare/compliance/hipaa.html",
         "HIPAA Compliance Guide",
         "Comprehensive guide to achieving and maintaining HIPAA compliance in enterprise healthcare technology systems."),
        ("services/enterprise/financial/compliance/sox.html",
         "solutions/enterprise/financial/compliance/sox.html",
         "SOX Compliance Guide",
         "Detailed guidance on Sarbanes-Oxley compliance for financial technology systems and reporting platforms."),
        ("services/enterprise/government/compliance/fedramp.html",
         "solutions/enterprise/government/compliance/fedramp.html",
         "FedRAMP Authorization Guide",
         "Step-by-step guide to achieving FedRAMP authorization for government cloud service providers."),
    ]

    for old_path, new_path, title, desc in deep_pages:
        sections = [
            ("Overview", f"<p>{desc}</p>"),
            ("Requirements", f"<p>This section outlines the key requirements for {title.lower()}, including technical controls, documentation, and audit procedures that must be in place.</p>"),
            ("Implementation Steps", f"<p>Follow these implementation steps to achieve and maintain compliance. Each step includes detailed guidance, evidence requirements, and common pitfalls to avoid.</p>"),
        ]
        depth = old_path.count("/")

        old_spec = PageSpec(path=old_path, title=title, description=desc,
                           sections=sections, css_classes=["overview", "requirements", "implementation"])
        old_pages.append(("old", old_spec, depth))

        similarity = 0.88
        new_sections = transform_content(sections, rng, similarity)
        new_depth = new_path.count("/")
        new_spec = PageSpec(path=new_path, title=title, description=desc,
                           sections=new_sections, css_classes=["overview", "requirements", "implementation"])
        new_pages.append(("new", new_spec, new_depth))

        mappings.append(MappingEntry(
            old_url=f"/{old_path}", new_url=f"/{new_path}",
            match_type="RESTRUCTURED", similarity="88%",
            stage="PairingStage",
            notes=f"Deep nesting ({depth}-level path)",
        ))

    # --- 8. One-to-many split (1 old -> 2 new) ---
    split_sections_combined = [
        ("Managed Infrastructure", "<p>Our managed infrastructure services ensure your cloud and on-premises systems run reliably with 24/7 monitoring, proactive maintenance, and rapid incident response.</p>"),
        ("Managed Security", "<p>Our managed security services protect your organization with continuous threat monitoring, vulnerability management, incident response, and compliance reporting.</p>"),
    ]

    old_spec = PageSpec(
        path="services/managed-services.html",
        title="Managed Services",
        description="TechCo Solutions managed infrastructure and security services.",
        sections=split_sections_combined,
        css_classes=["infrastructure", "security"],
    )
    old_pages.append(("old", old_spec, 1))

    # New page 1: Managed Infrastructure only
    new_spec_infra = PageSpec(
        path="solutions/managed-infra.html",
        title="Managed Infrastructure",
        description="TechCo managed infrastructure services.",
        sections=[transform_content([split_sections_combined[0]], rng, 0.85)[0]],
        css_classes=["infrastructure"],
    )
    new_pages.append(("new", new_spec_infra, 1))

    # New page 2: Managed Security only
    new_spec_sec = PageSpec(
        path="solutions/managed-security.html",
        title="Managed Security",
        description="TechCo managed security services.",
        sections=[transform_content([split_sections_combined[1]], rng, 0.85)[0]],
        css_classes=["security"],
    )
    new_pages.append(("new", new_spec_sec, 1))

    mappings.append(MappingEntry(
        old_url="/services/managed-services.html",
        new_url="/solutions/managed-infra.html",
        match_type="RESTRUCTURED", similarity="70%",
        stage="PairingStage",
        notes="Content split (1 old -> 2 new): infrastructure half",
    ))
    mappings.append(MappingEntry(
        old_url="/services/managed-services.html",
        new_url="/solutions/managed-security.html",
        match_type="RESTRUCTURED", similarity="65%",
        stage="PairingStage",
        notes="Content split (1 old -> 2 new): security half (secondary match)",
    ))

    # --- 9. Many-to-one merge (2 old -> 1 new) ---
    guide_part1_sections = [
        ("Planning Phase", "<p>This guide covers the planning and preparation phase of enterprise deployment, including requirements gathering, architecture design, and resource allocation.</p>"),
    ]
    guide_part2_sections = [
        ("Execution Phase", "<p>This guide covers the execution and validation phase of enterprise deployment, including installation procedures, configuration management, and testing protocols.</p>"),
    ]

    old_spec_p1 = PageSpec(
        path="resources/guide-part-1.html",
        title="Deployment Guide - Part 1: Planning",
        description="Enterprise deployment guide part 1: planning phase.",
        sections=guide_part1_sections,
        css_classes=["planning"],
    )
    old_pages.append(("old", old_spec_p1, 1))

    old_spec_p2 = PageSpec(
        path="resources/guide-part-2.html",
        title="Deployment Guide - Part 2: Execution",
        description="Enterprise deployment guide part 2: execution phase.",
        sections=guide_part2_sections,
        css_classes=["execution"],
    )
    old_pages.append(("old", old_spec_p2, 1))

    # Combined new page
    combined_sections = [
        ("Planning Phase", guide_part1_sections[0][1]),
        ("Execution Phase", guide_part2_sections[0][1]),
    ]
    new_spec_combined = PageSpec(
        path="resource-center/deployment-guide.html",
        title="Complete Deployment Guide",
        description="TechCo comprehensive deployment guide.",
        sections=transform_content(combined_sections, rng, 0.85),
        css_classes=["planning", "execution"],
    )
    new_pages.append(("new", new_spec_combined, 1))

    mappings.append(MappingEntry(
        old_url="/resources/guide-part-1.html",
        new_url="/resource-center/deployment-guide.html",
        match_type="RESTRUCTURED", similarity="70%",
        stage="PairingStage",
        notes="Content merge (2 old -> 1 new): part 1",
    ))
    mappings.append(MappingEntry(
        old_url="/resources/guide-part-2.html",
        new_url="/resource-center/deployment-guide.html",
        match_type="RESTRUCTURED", similarity="65%",
        stage="PairingStage",
        notes="Content merge (2 old -> 1 new): part 2",
    ))

    # --- 10. Near-duplicate confusion (2 old, 1 new) ---
    confusing_old_1_sections = [
        ("Cloud Migration Strategy", "<p>A comprehensive approach to migrating enterprise applications to the cloud. We help organizations assess their current state, develop migration strategies, and execute successful transitions with minimal disruption.</p>"),
        ("Assessment", "<p>Our assessment methodology evaluates application readiness, identifies dependencies, and recommends the optimal migration approach for each workload in your portfolio.</p>"),
    ]
    confusing_old_2_sections = [
        ("Cloud Migration Strategy", "<p>A thorough approach to moving enterprise applications to cloud platforms. We assist organizations in evaluating their existing infrastructure, creating migration plans, and carrying out successful transitions with minimal business impact.</p>"),
        ("Assessment", "<p>Our evaluation framework assesses application readiness, maps dependencies, and suggests the best migration path for each workload in your environment.</p>"),
    ]

    old_spec_c1 = PageSpec(
        path="services/cloud-migration.html",
        title="Cloud Migration Services",
        description="Cloud migration consulting and implementation services.",
        sections=confusing_old_1_sections,
        css_classes=["overview", "assessment"],
    )
    old_pages.append(("old", old_spec_c1, 1))

    old_spec_c2 = PageSpec(
        path="services/cloud-transition.html",
        title="Cloud Transition Services",
        description="Cloud transition consulting and execution services.",
        sections=confusing_old_2_sections,
        css_classes=["overview", "assessment"],
    )
    old_pages.append(("old", old_spec_c2, 1))

    # Single new page that both should match to
    new_spec_cloud = PageSpec(
        path="solutions/cloud-migration.html",
        title="Cloud Migration & Transition",
        description="TechCo cloud migration and transition services.",
        sections=transform_content(confusing_old_1_sections, rng, 0.90),
        css_classes=["overview", "assessment"],
    )
    new_pages.append(("new", new_spec_cloud, 1))

    mappings.append(MappingEntry(
        old_url="/services/cloud-migration.html",
        new_url="/solutions/cloud-migration.html",
        match_type="RENAMED", similarity="90%",
        stage="PairingStage",
        notes="Near-duplicate confusion: primary match",
    ))
    mappings.append(MappingEntry(
        old_url="/services/cloud-transition.html",
        new_url="/solutions/cloud-migration.html",
        match_type="MODERATE", similarity="80%",
        stage="PairingStage",
        notes="Near-duplicate confusion: secondary (ambiguous)",
    ))

    return old_pages, new_pages, mappings


def generate_orphaned_pages(rng: random.Random) -> tuple:
    """Generate 12 old-only pages (no match in new site)."""
    old_pages = []
    mappings = []

    orphan_topics = [
        ("resources/archive/legacy-docs.html", "Legacy Documentation Archive"),
        ("resources/archive/old-api-reference.html", "Legacy API Reference v1"),
        ("resources/archive/deprecated-features.html", "Deprecated Features List"),
        ("services/legacy-migration.html", "Legacy System Migration"),
        ("services/on-premises-support.html", "On-Premises Support"),
        ("partners/reseller-program.html", "Reseller Partner Program"),
        ("partners/affiliate-program.html", "Affiliate Program"),
        ("events/2022-annual-summit.html", "2022 Annual Summit Recap"),
        ("events/2023-spring-conference.html", "2023 Spring Conference"),
        ("resources/case-study-archive/2021-projects.html", "2021 Project Archive"),
        ("resources/case-study-archive/2020-projects.html", "2020 Project Archive"),
        ("misc/temp-landing.html", "Temporary Landing Page"),
    ]

    for path, title in orphan_topics:
        sections = [
            ("Overview", f"<p>This page covers {title.lower()} and provides historical context for past offerings and initiatives at TechCo Solutions.</p>"),
            ("Details", f"<p>Detailed information about {title.lower()} including background, scope, and relevant documentation for reference purposes.</p>"),
        ]
        depth = path.count("/")
        old_spec = PageSpec(
            path=path, title=title,
            description=f"TechCo Solutions: {title}",
            sections=sections, css_classes=["overview", "details"],
        )
        old_pages.append(("old", old_spec, depth))

        mappings.append(MappingEntry(
            old_url=f"/{path}", new_url="-",
            match_type="ORPHANED", similarity="0%",
            stage="None",
            notes="Old-only page, no new equivalent",
        ))

    return old_pages, [], mappings


def generate_new_only_pages(rng: random.Random) -> tuple:
    """Generate 12 new-only pages (no match in old site)."""
    new_pages = []
    mappings = []

    new_topics = [
        ("solutions/ai-services.html", "AI & Machine Learning Services", 1),
        ("solutions/platform-engineering.html", "Platform Engineering", 1),
        ("solutions/sustainability-tech.html", "Sustainability Technology", 1),
        ("products/product-gamma.html", "Product Gamma", 1),
        ("products/product-delta.html", "Product Delta", 1),
        ("resource-center/certifications.html", "Our Certifications", 1),
        ("resource-center/partners-directory.html", "Partners Directory", 1),
        ("help/support-tickets.html", "Support Tickets", 1),
        ("help/status-page.html", "System Status", 1),
        ("about-us/leadership-bios.html", "Leadership Bios", 1),
        ("about-us/company-history.html", "Company History", 1),
        ("about-us/awards.html", "Awards & Recognition", 1),
    ]

    for path, title, depth in new_topics:
        sections = [
            ("Overview", f"<p>{title} is a new addition to the TechCo website, providing information about our latest capabilities and offerings in this area.</p>"),
            ("Learn More", f"<p>Explore our {title.lower()} offerings and discover how TechCo can help your organization leverage the latest technologies and best practices.</p>"),
        ]

        new_spec = PageSpec(
            path=path, title=title,
            description=f"TechCo: {title}",
            sections=sections, css_classes=["overview", "learn-more"],
        )
        new_pages.append(("new", new_spec, depth))

        mappings.append(MappingEntry(
            old_url="-", new_url=f"/{path}",
            match_type="NEW", similarity="0%",
            stage="None",
            notes="New-only page, no old equivalent",
        ))

    return [], new_pages, mappings


# ============================================================
# Mapping Tracker & File Writer
# ============================================================

def generate_sitemap_xml(site_dir: Path, base_url: str) -> str:
    """
    Scan site_dir for all HTML files and generate a sitemap.xml.
    Also generates a flat CSV of all URLs for direct pipeline input.
    """
    urls = []
    for filepath in sorted(site_dir.rglob("*.html")):
        rel = filepath.relative_to(site_dir).as_posix()
        urls.append(f"{base_url}/{rel}")

    # Write sitemap.xml
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        lines.append(f"  <url><loc>{url}</loc></url>")
    # Include non-HTML assets so UrlPruneStage can filter them
    for filepath in sorted(site_dir.rglob("*")):
        if filepath.is_file() and filepath.suffix in (".css", ".js", ".png", ".jpg", ".svg", ".pdf", ".webp"):
            rel = filepath.relative_to(site_dir).as_posix()
            asset_url = f"{base_url}/{rel}"
            if asset_url not in urls:
                lines.append(f"  <url><loc>{asset_url}</loc></url>")
                urls.append(asset_url)
    lines.append("</urlset>")

    sitemap_content = "\n".join(lines)
    sitemap_path = site_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap_content, encoding="utf-8")

    # Write flat URL list CSV for direct pipeline input
    csv_path = site_dir / "urls.csv"
    csv_path.write_text("\n".join(urls) + "\n", encoding="utf-8")

    return sitemap_path, csv_path, len(urls)


def write_mapping_md(all_mappings: list, existing_count: int, output_path: Path):
    """Write the expanded SITE_MAPPING_EXPANDED.md."""
    lines = [
        "# Expanded Site Mapping Matrix",
        "",
        "This document extends the original SITE_MAPPING.md with additional generated pages",
        "for stress-testing the Redirx pipeline. Generated by `scripts/expand_mock_sites.py`.",
        "",
        "## Mapping Legend",
        "",
        "**Match Types:**",
        "- `IDENTICAL` - Exact same HTML content (byte-for-byte)",
        "- `SIMILAR` - High content similarity (85-95%) with minor rewrites",
        "- `MODERATE` - Moderate similarity (60-85%) with significant rewrites",
        "- `RENAMED` - URL changed but content highly similar",
        "- `RESTRUCTURED` - Major URL pattern changes with similar content",
        "- `ORPHANED` - Old page with no equivalent in new site",
        "- `NEW` - New page with no equivalent in old site",
        "- `FILTERED` - Asset or blog post filtered by pipeline stage",
        "",
        "**Pipeline Stages:**",
        "- `UrlPruneStage` - Filters non-HTML files",
        "- `BlogPruneStage` - Filters individual blog posts",
        "- `ExactUrlMatchStage` - Matches identical URL paths",
        "- `HtmlPruneStage` - Matches pages with identical HTML",
        "- `PairingStage` - Matches via vector similarity",
        "",
        "## Generated Page Mappings",
        "",
        "| # | Old URL | New URL | Match Type | Similarity | Stage | Notes |",
        "|---|---------|---------|------------|------------|-------|-------|",
    ]

    for i, m in enumerate(all_mappings, 1):
        lines.append(f"| {i} | {m.old_url} | {m.new_url} | {m.match_type} | {m.similarity} | {m.stage} | {m.notes} |")

    # Summary statistics
    match_type_counts = {}
    stage_counts = {}
    similarity_counts = {"100%": 0, "90-99%": 0, "80-89%": 0, "70-79%": 0,
                         "60-69%": 0, "40-59%": 0, "0%": 0, "N/A": 0}

    for m in all_mappings:
        match_type_counts[m.match_type] = match_type_counts.get(m.match_type, 0) + 1
        stage_counts[m.stage] = stage_counts.get(m.stage, 0) + 1

        if m.similarity == "N/A":
            similarity_counts["N/A"] += 1
        elif m.similarity == "0%":
            similarity_counts["0%"] += 1
        elif m.similarity == "100%":
            similarity_counts["100%"] += 1
        else:
            pct = int(m.similarity.rstrip("%"))
            if pct >= 90:
                similarity_counts["90-99%"] += 1
            elif pct >= 80:
                similarity_counts["80-89%"] += 1
            elif pct >= 70:
                similarity_counts["70-79%"] += 1
            elif pct >= 60:
                similarity_counts["60-69%"] += 1
            else:
                similarity_counts["40-59%"] += 1

    lines.extend([
        "",
        "## Summary Statistics",
        "",
        "### By Match Type",
    ])
    for mt, count in sorted(match_type_counts.items()):
        lines.append(f"- **{mt}:** {count} pages")

    lines.extend([
        "",
        "### By Pipeline Stage",
    ])
    for stage, count in sorted(stage_counts.items()):
        lines.append(f"- **{stage}:** {count} pages")

    lines.extend([
        "",
        "### By Content Similarity",
    ])
    for band, count in similarity_counts.items():
        if count > 0:
            lines.append(f"- **{band}:** {count} pages")

    lines.extend([
        "",
        f"### Page Counts",
        f"- **Generated mappings:** {len(all_mappings)} rows",
        f"- **Existing original pages:** ~{existing_count} (see SITE_MAPPING.md)",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_mapping_csv(all_mappings: list, output_path: Path):
    """Write expected_mappings.csv for programmatic use."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["old_url", "new_url", "match_type", "similarity", "stage", "notes"])
        for m in all_mappings:
            writer.writerow([m.old_url, m.new_url, m.match_type, m.similarity, m.stage, m.notes])


def clean_generated_files(site_dir: Path, dry_run: bool = False) -> int:
    """Remove all files containing the generation marker. Returns count removed."""
    removed = 0
    if not site_dir.exists():
        return 0

    for filepath in site_dir.rglob("*"):
        if not filepath.is_file():
            continue

        try:
            # Check text files for marker
            if filepath.suffix in (".html", ".htm"):
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                if MARKER in content:
                    if dry_run:
                        print(f"  Would remove: {safe_str(filepath)}")
                    else:
                        filepath.unlink()
                    removed += 1
            # Check binary/asset files for marker in a different way
            # Generated assets are in specific known paths; we check if parent dirs are empty
            elif filepath.suffix in (".pdf", ".svg", ".webp"):
                content = filepath.read_bytes()
                # Asset placeholders are small, check size
                if len(content) < 200:
                    if dry_run:
                        print(f"  Would remove: {safe_str(filepath)}")
                    else:
                        filepath.unlink()
                    removed += 1
        except Exception as e:
            print(f"  Warning: Could not check {safe_str(filepath)}: {e}")

    # Clean up empty directories (bottom-up)
    if not dry_run:
        for dirpath in sorted(site_dir.rglob("*"), reverse=True):
            if dirpath.is_dir():
                try:
                    # Only remove if empty
                    if not any(dirpath.iterdir()):
                        dirpath.rmdir()
                except Exception:
                    pass

    return removed


def write_page(site_dir: Path, page_entry: tuple, identical_html_cache: dict):
    """
    Write a single page to disk.
    page_entry is (type, spec_or_path, depth_or_data)
    """
    page_type = page_entry[0]

    if page_type == "old":
        spec = page_entry[1]
        depth = page_entry[2]
        html = render_old_page(spec, depth)
        filepath = site_dir / spec.path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(html, encoding="utf-8")
        # Cache for identical_html pairs
        identical_html_cache[spec.path] = html

    elif page_type == "new":
        spec = page_entry[1]
        depth = page_entry[2]
        html = render_new_page(spec, depth)
        filepath = site_dir / spec.path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(html, encoding="utf-8")

    elif page_type == "identical_html":
        # Render with old template to get byte-identical HTML
        spec = page_entry[1]
        depth = page_entry[2]
        html = render_old_page(spec, depth)
        filepath = site_dir / spec.path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(html, encoding="utf-8")

    elif page_type == "raw_html":
        path = page_entry[1]
        html = page_entry[2]
        filepath = site_dir / path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(html, encoding="utf-8")

    elif page_type == "asset":
        path = page_entry[1]
        data = page_entry[2]
        filepath = site_dir / path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(data)


# ============================================================
# Main Orchestration
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Expand mock sites with generated pages for Redirx pipeline testing."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be generated without writing files")
    parser.add_argument("--clean", action="store_true",
                        help="Remove all generated pages (preserves originals)")
    parser.add_argument("--old-dir", type=Path, default=DEFAULT_OLD_DIR,
                        help=f"Old site directory (default: {DEFAULT_OLD_DIR})")
    parser.add_argument("--new-dir", type=Path, default=DEFAULT_NEW_DIR,
                        help=f"New site directory (default: {DEFAULT_NEW_DIR})")

    args = parser.parse_args()

    # Handle --clean mode
    if args.clean:
        print("Cleaning generated files...")
        old_removed = clean_generated_files(args.old_dir, dry_run=False)
        new_removed = clean_generated_files(args.new_dir, dry_run=False)
        print(f"Removed {old_removed} files from old site")
        print(f"Removed {new_removed} files from new site")

        # Also remove mapping files and generated sitemaps/URL lists
        cleanup_files = [
            MAPPING_MD_PATH, MAPPING_CSV_PATH,
            args.old_dir / "sitemap.xml", args.old_dir / "urls.csv",
            args.new_dir / "sitemap.xml", args.new_dir / "urls.csv",
        ]
        for f in cleanup_files:
            if f.exists():
                f.unlink()
                print(f"Removed {safe_str(f)}")

        print("Clean complete.")
        return

    # Seed RNG for deterministic output
    rng = random.Random(SEED)

    print("Generating mock site pages...")
    print(f"Old site dir: {safe_str(args.old_dir)}")
    print(f"New site dir: {safe_str(args.new_dir)}")
    print()

    # Collect all pages from generators
    all_old_pages = []
    all_new_pages = []
    all_mappings = []

    generators = [
        ("Team Bios", generate_team_bios),
        ("Industry Verticals", generate_industry_pages),
        ("Locations", generate_location_pages),
        ("KB Articles", generate_kb_articles),
        ("Events", generate_events),
        ("Integrations", generate_integrations),
        ("Portfolio", generate_portfolio),
        ("Blog Posts", generate_blog_posts),
        ("Section Indexes", generate_section_indexes),
        ("Edge Cases", generate_edge_cases),
        ("Orphaned Pages", generate_orphaned_pages),
        ("New-Only Pages", generate_new_only_pages),
    ]

    for name, gen_func in generators:
        old_pages, new_pages, mappings = gen_func(rng)
        all_old_pages.extend(old_pages)
        all_new_pages.extend(new_pages)
        all_mappings.extend(mappings)

        old_count = len(old_pages)
        new_count = len(new_pages)
        print(f"  {name}: {old_count} old + {new_count} new pages, {len(mappings)} mappings")

    # Count by type
    old_html = sum(1 for p in all_old_pages if p[0] in ("old", "raw_html", "identical_html"))
    old_assets = sum(1 for p in all_old_pages if p[0] == "asset")
    new_html = sum(1 for p in all_new_pages if p[0] in ("new", "raw_html", "identical_html"))
    new_assets = sum(1 for p in all_new_pages if p[0] == "asset")

    print()
    print(f"Total generated: {len(all_old_pages)} old ({old_html} HTML + {old_assets} assets)")
    print(f"                 {len(all_new_pages)} new ({new_html} HTML + {new_assets} assets)")
    print(f"Total mappings:  {len(all_mappings)}")

    if args.dry_run:
        print()
        print("DRY RUN - no files written.")
        print()
        print("Files that would be generated:")
        print(f"\nOld site ({safe_str(args.old_dir)}):")
        for p in sorted(all_old_pages, key=lambda x: x[1].path if hasattr(x[1], 'path') else x[1]):
            path = p[1].path if hasattr(p[1], 'path') else p[1]
            print(f"  {path}")
        print(f"\nNew site ({safe_str(args.new_dir)}):")
        for p in sorted(all_new_pages, key=lambda x: x[1].path if hasattr(x[1], 'path') else x[1]):
            path = p[1].path if hasattr(p[1], 'path') else p[1]
            print(f"  {path}")

        # Also show what would be cleaned
        print()
        print("Pre-existing generated files that would be cleaned:")
        clean_generated_files(args.old_dir, dry_run=True)
        clean_generated_files(args.new_dir, dry_run=True)
        return

    # Clean existing generated files first (idempotency)
    print()
    print("Cleaning existing generated files...")
    old_cleaned = clean_generated_files(args.old_dir)
    new_cleaned = clean_generated_files(args.new_dir)
    if old_cleaned or new_cleaned:
        print(f"  Cleaned {old_cleaned} old + {new_cleaned} new generated files")

    # Write all pages
    print("Writing pages...")
    identical_html_cache = {}

    for page in all_old_pages:
        write_page(args.old_dir, page, identical_html_cache)

    for page in all_new_pages:
        write_page(args.new_dir, page, identical_html_cache)

    # Generate sitemaps and URL lists for both sites
    print("Generating sitemaps and URL lists...")
    old_sm, old_csv, old_url_count = generate_sitemap_xml(args.old_dir, "http://localhost:8000")
    new_sm, new_csv, new_url_count = generate_sitemap_xml(args.new_dir, "http://localhost:8001")
    print(f"  Old site: {old_url_count} URLs in sitemap.xml and urls.csv")
    print(f"  New site: {new_url_count} URLs in sitemap.xml and urls.csv")

    # Write mapping documents
    print("Writing mapping documents...")
    write_mapping_md(all_mappings, existing_count=36, output_path=MAPPING_MD_PATH)
    write_mapping_csv(all_mappings, output_path=MAPPING_CSV_PATH)

    # Final summary
    print()
    print("=" * 60)
    print("Generation complete!")
    print(f"  Old site pages written: {len(all_old_pages)}")
    print(f"  New site pages written: {len(all_new_pages)}")
    print(f"  Mapping MD:  {safe_str(MAPPING_MD_PATH)}")
    print(f"  Mapping CSV: {safe_str(MAPPING_CSV_PATH)}")
    print()

    # Count existing original files
    existing_old = sum(1 for _ in args.old_dir.rglob("*.html")
                       if MARKER not in _.read_text(encoding="utf-8", errors="ignore"))
    existing_new = sum(1 for _ in args.new_dir.rglob("*.html")
                       if MARKER not in _.read_text(encoding="utf-8", errors="ignore"))

    print(f"  Grand total old site: ~{existing_old + old_html} HTML pages ({existing_old} original + {old_html} generated)")
    print(f"  Grand total new site: ~{existing_new + new_html} HTML pages ({existing_new} original + {new_html} generated)")
    print()
    print("  Pipeline-ready URL lists (upload these CSVs to Redirx):")
    print(f"    Old: {safe_str(args.old_dir / 'urls.csv')}")
    print(f"    New: {safe_str(args.new_dir / 'urls.csv')}")
    print()
    print("  Sitemaps (for crawlers):")
    print(f"    Old: {safe_str(args.old_dir / 'sitemap.xml')}")
    print(f"    New: {safe_str(args.new_dir / 'sitemap.xml')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
