
// Auto-refresh stats every 30 seconds
function refreshStats() {
    fetch('/api/stats')
        .then(response => response.json())
        .then(data => {
            // Update stats cards if on dashboard
            const statsCards = document.querySelectorAll('[data-stat]');
            statsCards.forEach(card => {
                const statType = card.getAttribute('data-stat');
                if (data[statType] !== undefined) {
                    card.textContent = data[statType];
                }
            });
        })
        .catch(error => console.log('Error refreshing stats:', error));
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Auto-refresh stats if on dashboard
    if (window.location.pathname === '/') {
        setInterval(refreshStats, 30000);
    }
    
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            if (alert && alert.parentNode) {
                alert.remove();
            }
        }, 5000);
    });
    
    // Add loading states to form submissions
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function() {
            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
            }
        });
    });
});

// Utility function to show confirmation dialogs
function confirmAction(message) {
    return confirm(message);
}
