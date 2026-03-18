/**
 * beams-bg.js — FFL animated beam background
 * Vanilla JS port of BeamsBackground React component
 * Supports login page (.login-wrapper) and portal interior (.app-wrapper)
 */

(function () {
  'use strict';

  const BEAMS = [
    { x: 0.10, y: -0.05, angle: 218, width: 2.2, hue: 204, speed: 0.38, opacity: 0.55 },
    { x: 0.35, y: -0.15, angle: 225, width: 3.0, hue: 210, speed: 0.28, opacity: 0.45 },
    { x: 0.60, y: -0.08, angle: 212, width: 1.6, hue: 198, speed: 0.52, opacity: 0.65 },
    { x: 0.82, y:  0.02, angle: 232, width: 2.5, hue: 207, speed: 0.33, opacity: 0.50 },
    { x: 0.20, y:  0.40, angle: 220, width: 1.9, hue: 196, speed: 0.44, opacity: 0.38 },
    { x: 0.50, y:  0.25, angle: 228, width: 2.8, hue: 213, speed: 0.31, opacity: 0.42 },
    { x: 0.75, y:  0.55, angle: 215, width: 1.4, hue: 202, speed: 0.58, opacity: 0.35 },
  ];

  /**
   * @param {HTMLElement} containerEl
   * @param {object}      [options]
   * @param {number}      [options.opacityScale=1.0]  — multiply all beam opacities (0–1)
   * @param {boolean}     [options.fixed=false]       — use position:fixed (portal) vs absolute (login)
   */
  function initBeams(containerEl, options) {
    options = options || {};
    const opacityScale = (options.opacityScale !== undefined) ? options.opacityScale : 1.0;
    const useFixed     = !!options.fixed;

    const canvas = document.createElement('canvas');
    canvas.style.cssText = [
      'position: ' + (useFixed ? 'fixed' : 'absolute'),
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
      if (useFixed) {
        canvas.width  = window.innerWidth;
        canvas.height = window.innerHeight;
      } else {
        canvas.width  = containerEl.offsetWidth;
        canvas.height = containerEl.offsetHeight;
      }
    }

    function drawBackground() {
      const w = canvas.width;
      const h = canvas.height;
      const cx = w * 0.5;
      const cy = h * 0.45;
      const radius = Math.max(w, h) * 0.75;

      const radial = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      radial.addColorStop(0,   'rgba(30,157,241,0.15)');
      radial.addColorStop(0.5, 'rgba(30,157,241,0.04)');
      radial.addColorStop(1,   'rgba(8,8,8,0)');

      ctx.fillStyle = useFixed ? 'rgba(0,0,0,0)' : '#080808';
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

      const pulseOpacity = beam.opacity * opacityScale * (0.7 + 0.3 * Math.sin(tick * beam.speed));

      const ox = beam.x * w;
      const oy = beam.y * h;
      const rad = (beam.angle * Math.PI) / 180;

      ctx.save();
      ctx.translate(ox, oy);
      ctx.rotate(rad);

      const startOffset = -beamLength * 0.25;

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

    const ro = new ResizeObserver(() => resize());
    ro.observe(useFixed ? document.body : containerEl);
    resize();

    window.addEventListener('resize', resize);

    frame();

    return function destroy() {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      window.removeEventListener('resize', resize);
      canvas.remove();
    };
  }

  function autoInit() {
    // Login page
    const login = document.querySelector('.login-wrapper');
    if (login) {
      initBeams(login, { opacityScale: 1.0, fixed: false });
      return;
    }
    // Portal interior (admin/client/trainer)
    const app = document.querySelector('.app-wrapper');
    if (app) {
      initBeams(app, { opacityScale: 0.12, fixed: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
  } else {
    autoInit();
  }

  window.initBeams = initBeams;
})();
