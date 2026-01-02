function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const target = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', target);
    localStorage.setItem('theme', target);
}

// Progress Checkbox Logic
document.querySelectorAll('.progress-check').forEach(checkbox => {
    checkbox.addEventListener('change', function() {
        const concept = this.dataset.concept;
        const groupId = this.dataset.group;
        const isChecked = this.checked;

        fetch('/update-progress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                group_id: groupId,
                concept: concept,
                status: isChecked
            })
        });
    });
});