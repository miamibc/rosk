const lightbox = document.querySelector('.lightbox');
const image = lightbox.querySelector('img');

if (typeof lightbox.showModal === 'function') {
  for (const link of document.querySelectorAll('.screenshot')) {
    link.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;

      event.preventDefault();
      image.src = link.href;
      image.alt = link.querySelector('img').alt;
      lightbox.showModal();
    });
  }

  lightbox.addEventListener('click', (event) => {
    if (event.target === lightbox) lightbox.close();
  });
}
