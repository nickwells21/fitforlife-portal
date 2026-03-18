(function () {
  'use strict';

  function initGallery() {
    var galleryEl = document.querySelector('.ffl-gallery');
    if (!galleryEl) return;

    var viewport = galleryEl.querySelector('.ffl-gallery__viewport');
    if (!viewport) return;

    // Wait for EmblaCarousel to be available
    if (typeof EmblaCarousel === 'undefined') return;

    var embla = EmblaCarousel(viewport, {
      loop: false,
      align: 'start',
      skipSnaps: false,
      dragFree: false,
      containScroll: 'trimSnaps'
    });

    var prevBtn = document.querySelector('.ffl-gallery__prev');
    var nextBtn = document.querySelector('.ffl-gallery__next');

    function updateButtons() {
      if (!prevBtn || !nextBtn) return;
      var canPrev = embla.canScrollPrev();
      var canNext = embla.canScrollNext();

      prevBtn.disabled = !canPrev;
      nextBtn.disabled = !canNext;
      prevBtn.style.opacity = canPrev ? '1' : '0.35';
      nextBtn.style.opacity = canNext ? '1' : '0.35';
      prevBtn.style.cursor = canPrev ? 'pointer' : 'not-allowed';
      nextBtn.style.cursor = canNext ? 'pointer' : 'not-allowed';
    }

    if (prevBtn) {
      prevBtn.addEventListener('click', function () {
        embla.scrollPrev();
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener('click', function () {
        embla.scrollNext();
      });
    }

    embla.on('init', updateButtons);
    embla.on('select', updateButtons);
    embla.on('reInit', updateButtons);

    // Initial button state
    updateButtons();

    // Keyboard arrow support when focus is inside the gallery section
    var section = document.querySelector('.ffl-gallery-section');
    if (section) {
      section.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft') {
          e.preventDefault();
          embla.scrollPrev();
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          embla.scrollNext();
        }
      });
      // Make section focusable for keyboard events
      if (!section.hasAttribute('tabindex')) {
        section.setAttribute('tabindex', '0');
      }
    }
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGallery);
  } else {
    initGallery();
  }
})();
