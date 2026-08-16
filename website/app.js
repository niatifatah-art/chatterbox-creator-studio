const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (reducedMotion || !('IntersectionObserver' in window)) {
  document.querySelectorAll('.reveal').forEach((node) => node.classList.add('visible'));
} else {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    }
  }, { threshold: 0.13, rootMargin: '0px 0px -35px' });

  document.querySelectorAll('.reveal').forEach((node) => observer.observe(node));
}

// Keep the small interface mock interactive enough to communicate hierarchy without
// pretending it is the live desktop application.
document.querySelectorAll('.frame-tabs button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.frame-tabs button').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
  });
});
