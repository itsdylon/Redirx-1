// TechCo - Main JavaScript

document.addEventListener('DOMContentLoaded', function() {
    console.log('TechCo website loaded');

    // Simple navigation highlighting
    highlightCurrentPage();

    // Add smooth scrolling to anchor links
    addSmoothScrolling();

    // Initialize any interactive elements
    initializeInteractiveElements();
});

/**
 * Highlight the current page in navigation
 */
function highlightCurrentPage() {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('header nav a');

    navLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath ||
            link.getAttribute('href') === currentPath.split('/').pop()) {
            link.style.borderBottom = '2px solid #3498db';
        }
    });
}

/**
 * Add smooth scrolling behavior to anchor links
 */
function addSmoothScrolling() {
    const anchorLinks = document.querySelectorAll('a[href^="#"]');

    anchorLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const targetId = this.getAttribute('href').substring(1);
            const targetElement = document.getElementById(targetId);

            if (targetElement) {
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
}

/**
 * Initialize interactive elements
 */
function initializeInteractiveElements() {
    // Add hover effects to service items
    const serviceItems = document.querySelectorAll('.service-item');
    serviceItems.forEach(item => {
        item.addEventListener('mouseenter', function() {
            this.style.transform = 'translateY(-5px)';
            this.style.transition = 'transform 0.3s';
        });

        item.addEventListener('mouseleave', function() {
            this.style.transform = 'translateY(0)';
        });
    });

    // Add click tracking for CTA buttons (for analytics)
    const ctaButtons = document.querySelectorAll('.cta-button');
    ctaButtons.forEach(button => {
        button.addEventListener('click', function() {
            console.log('CTA clicked:', this.href);
        });
    });
}
