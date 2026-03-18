/**
 * beams-bg.js — FFL Login page animated beam background
 * Vanilla JS port of BeamsBackground React component
 */

(function () {
  'use strict';

  const BEAMS = [
    { x: 0.10, y: -0.05, angle: 218, width: 2.2, hue: 247, speed: 0.38, opacity: 0.55 },
    { x: 0.35, y: -0.15, angle: 225, width: 3.0, hue: 258, speed: 0.28, opacity: 0.45 },
    { x: 0.60, y: -0.08, angle: 212, width: 1.6, hue: 242, speed: 0.52, opacity: 0.65 },
    { x: 0.82, y:  0.02, angle: 232, width: 2.5, hue: 252, speed: 0.33, opacity: 0.50 },
    { x: 0.20, y:  0.40, angle: 220, width: 1.9, hue: 238, speed: 0.44, opacity: 0.38 },
    { x: 0.50, y:  0.25, angle: 228, width: 2.8, hue: 262, speed: 0.31, opacity: 0.42 },
    { x: 0.75, y:  0.55, angle: 215, width: 1.4, hue: 245, speed: 0.58, opacity: 0.35 },
  ];

  function initBeams(containerEl) {
    // Create canvas and insert it as the first child of the container
    const canvas = document.createElement('canvas');
    canvas.style.cssText = [
      'position: absolute',
      'inset: 0',
      'width: 100%',
      'height: 100%',
      'pointer-events: none',
      'z-index: 0',
    ].join(';');
    containerEl.insertBefore(canvas, containerEl.firstChild);

    const ctx = canvas.getContext('2d');
    let tick = 0;
    let rafId = null;

    function resize() {
      canvas.width  = containerEl.offsetWidth;
      canvas.height = containerEl.offsetHeight;
    }

    function drawBackground() {
      const w = canvas.width;
      const h = canvas.height;
      const cx = w * 0.5;
      const cy = h * 0.45;
      const radius = Math.max(w, h) * 0.75;

      const radial = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      radial.addColorStop(0,   'rgba(99,102,241,0.15)');
      radial.addColorStop(0.5, 'rgba(99,102,241,0.04)');
      radial.addColorStop(1,   'rgba(8,8,8,0)');

      ctx.fillStyle = '#080808';
      ctx.fillRect(0, 0, w, h);

      ctx.fillStyle = radial;
      ctx.fillRect(0, 0, w, h);
    }

    function drawBeam(beam) {
      const w = canvas.width;
      const h = canvas.height;
      const diag = Math.sqrt(w * w + h * h);
      const beamLength = diag * 1.5;
      const beamWidth  = Math.min(w, h) * (beam.width / 100);

      // Pulse opacity with sin
      const pulseOpacity = beam.opacity * (0.7 + 0.3 * Math.sin(tick * beam.speed));

      // Origin in canvas coordinates
      const ox = beam.x * w;
      const oy = beam.y * h;

      // Convert angle to radians (angle is the direction the beam travels)
      const rad = (beam.angle * Math.PI) / 180;

      ctx.save();
      ctx.translate(ox, oy);
      ctx.rotate(rad);

      // The beam rect: starts well behind origin, extends beamLength forward
      // We centre the beam width on the travel axis
      const startOffset = -beamLength * 0.25;

      // Create gradient along the beam length (local x after rotation = along beam)
      const grad = ctx.createLinearGradient(startOffset, 0, startOffset + beamLength, 0);
      grad.addColorStop(0,    `hsla(${beam.hue},85%,65%,0)`);
      grad.addColorStop(0.10, `hsla(${beam.hue},85%,65%,0)`);
      grad.addColorStop(0.15, `hsla(${beam.hue},85%,65%,${pulseOpacity})`);
      grad.addColorStop(0.45, `hsla(${beam.hue},85%,65%,${pulseOpacity * 0.6})`);
      grad.addColorStop(0.75, `hsla(${beam.hue},85%,65%,${pulseOpacity * 0.15})`);
      grad.addColorStop(1,    `hsla(${beam.hue},85%,65%,0)`);

      ctx.fillStyle = grad;
      ctx.fillRect(startOffset, -beamWidth * 0.5, beamLength, beamWidth);

      ctx.restore();
    }

    function frame() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawBackground();
      for (const beam of BEAMS) {
        drawBeam(beam);
      }
      tick += 0.006;
      rafId = requestAnimationFrame(frame);
    }

    // Handle resize
    const ro = new ResizeObserver(() => resize());
    ro.observe(containerEl);
    resize();

    // Also handle window resize as fallback
    window.addEventListener('resize', resize);

    frame();

    // Return teardown function
    return function destroy() {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      window.removeEventListener('resize', resize);
      canvas.remove();
    };
  }

  // Auto-init when DOM is ready
  function autoInit() {
    const wrapper = document.querySelector('.login-wrapper');
    if (wrapper) {
      initBeams(wrapper);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
  } else {
    autoInit();
  }

  // Expose for manual usage
  window.initBeams = initBeams;
})();
