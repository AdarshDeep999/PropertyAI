/* Atmosphere — glossy floating spheres for the studio backdrop.
   A deliberately small WebGL renderer (two triangles per sphere, ray-sphere
   shading in the fragment shader) instead of a 3D engine: the whole effect is
   about 4 draw calls and 300 lines, works offline, and respects reduced motion.

   If WebGL is unavailable the CSS gradient orbs in the page still carry the
   look, so nothing is missing. */
(function () {
  "use strict";

  const canvas = document.getElementById("atmosphere-canvas");
  if (!canvas) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const spheres = [
    // x, y as viewport fractions; r as a fraction of the smaller viewport side
    { x: 0.87, y: 0.13, r: 0.185, depth: 0.9, glass: 0.25, deep: [0.020, 0.078, 0.050], mid: [0.176, 0.462, 0.310], light: [0.878, 0.976, 0.808], drift: 26, phase: 0.4 },
    { x: 0.015, y: 0.88, r: 0.1, depth: 0.55, glass: 0.12, deep: [0.016, 0.066, 0.042], mid: [0.290, 0.400, 0.220], light: [0.800, 0.930, 0.720], drift: 18, phase: 2.1 },
    { x: 0.72, y: 0.88, r: 0.062, depth: 0.35, glass: 0.55, deep: [0.030, 0.110, 0.074], mid: [0.400, 0.640, 0.470], light: [0.910, 1.000, 0.880], drift: 14, phase: 4.0 },
    { x: 0.965, y: 0.62, r: 0.032, depth: 0.2, glass: 0.3, deep: [0.024, 0.094, 0.062], mid: [0.352, 0.560, 0.372], light: [0.870, 0.980, 0.820], drift: 10, phase: 5.2 },
  ];

  const vertexSource = `
    attribute vec2 aPos;
    uniform vec2 uCenter;
    uniform float uRadius;
    uniform vec2 uResolution;
    varying vec2 vP;

    void main() {
      vP = aPos;
      vec2 px = uCenter + aPos * uRadius * 2.35;
      vec2 clip = (px / uResolution) * 2.0 - 1.0;
      gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
    }
  `;

  const fragmentSource = `
    precision highp float;

    varying vec2 vP;
    uniform float uTime;
    uniform float uGlass;
    uniform vec3 uDeep;
    uniform vec3 uMid;
    uniform vec3 uLight;

    void main() {
      // Quad space -> sphere radii. The sphere itself occupies |p| < 1.
      vec2 p = vP * 2.35;
      float d = length(p);

      if (d > 1.0) {
        // Soft atmospheric halo around the sphere.
        float halo = smoothstep(1.42, 1.0, d) * 0.18;
        halo *= 0.75 + 0.25 * sin(uTime * 0.6 + d * 6.0);
        gl_FragColor = vec4(uLight, halo);
        return;
      }

      float z = sqrt(max(0.0, 1.0 - d * d));
      vec3 n = vec3(p.x, -p.y, z);

      // Reflection sampled from a procedural studio environment:
      // dark forest below, emerald horizon, mint light above.
      vec3 refl = reflect(vec3(0.0, 0.0, -1.0), n);
      float t = clamp(refl.y * 0.5 + 0.5, 0.0, 1.0);
      vec3 env = mix(uDeep, uMid, smoothstep(0.0, 0.58, t));
      env = mix(env, uLight, smoothstep(0.56, 1.0, t));
      env += uMid * 0.32 * pow(clamp(refl.x, 0.0, 1.0), 2.0);

      // Two soft studio highlights: a large key light and a rim light.
      vec3 keyDir = normalize(vec3(-0.5 + 0.06 * sin(uTime * 0.35), 0.72, 0.52));
      float key = pow(max(dot(n, keyDir), 0.0), 58.0);
      vec3 rimDir = normalize(vec3(0.62, -0.34, 0.68));
      float rim = pow(max(dot(n, rimDir), 0.0), 16.0);

      float fresnel = pow(1.0 - z, 3.2);
      vec3 color = env * (0.66 + 0.34 * z);
      color += vec3(1.0, 0.99, 0.94) * key * (0.85 - uGlass * 0.35);
      color += uLight * rim * (0.18 + uGlass * 0.22);
      color = mix(color, uLight, fresnel * (0.38 + uGlass * 0.22));

      // Inner glow and a grounded falloff at the base.
      color += uMid * 0.16 * smoothstep(0.75, 0.0, d);
      color *= 0.84 + 0.16 * smoothstep(-0.9, 0.65, p.y);

      float alpha = (1.0 - uGlass * 0.42) * smoothstep(1.0, 0.93, d);
      gl_FragColor = vec4(color, alpha);
    }
  `;

  let gl = null;
  try {
    gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
    });
  } catch (error) {
    gl = null;
  }
  if (!gl) {
    canvas.style.display = "none";
    return;
  }

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      console.warn("Atmosphere shader failed:", gl.getShaderInfoLog(shader));
      gl.deleteShader(shader);
      return null;
    }
    return shader;
  }

  const vertexShader = compile(gl.VERTEX_SHADER, vertexSource);
  const fragmentShader = compile(gl.FRAGMENT_SHADER, fragmentSource);
  if (!vertexShader || !fragmentShader) {
    canvas.style.display = "none";
    return;
  }

  const program = gl.createProgram();
  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("Atmosphere program failed:", gl.getProgramInfoLog(program));
    canvas.style.display = "none";
    return;
  }
  gl.useProgram(program);

  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
    gl.STATIC_DRAW
  );

  const aPos = gl.getAttribLocation(program, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  const uCenter = gl.getUniformLocation(program, "uCenter");
  const uRadius = gl.getUniformLocation(program, "uRadius");
  const uResolution = gl.getUniformLocation(program, "uResolution");
  const uTime = gl.getUniformLocation(program, "uTime");
  const uGlass = gl.getUniformLocation(program, "uGlass");
  const uDeep = gl.getUniformLocation(program, "uDeep");
  const uMid = gl.getUniformLocation(program, "uMid");
  const uLight = gl.getUniformLocation(program, "uLight");

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  let width = 0;
  let height = 0;
  let dpr = 1;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    width = canvas.clientWidth || window.innerWidth;
    height = canvas.clientHeight || window.innerHeight;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    draw(performance.now());
  }

  let lastFrame = 0;
  function draw(now) {
    const time = (now || 0) / 1000;
    const scroll = window.scrollY || 0;
    const minSide = Math.min(width, height);

    gl.clear(gl.COLOR_BUFFER_BIT);

    spheres.forEach((sphere, index) => {
      const floatX = Math.sin(time * 0.22 + sphere.phase) * sphere.drift;
      const floatY = Math.cos(time * 0.17 + sphere.phase * 1.3) * sphere.drift;
      const scrollShift = scroll * 0.045 * sphere.depth;
      const parallaxX = pointer.x * 26 * sphere.depth;
      const parallaxY = pointer.y * 18 * sphere.depth;

      const cx = sphere.x * width + floatX + parallaxX;
      const cy = sphere.y * height + floatY + parallaxY - scrollShift;
      const radius = Math.max(6, sphere.r * minSide);

      gl.uniform2f(uCenter, cx * dpr, cy * dpr);
      gl.uniform1f(uRadius, radius * dpr);
      gl.uniform2f(uResolution, canvas.width, canvas.height);
      gl.uniform1f(uTime, time + index * 3.7);
      gl.uniform1f(uGlass, sphere.glass);
      gl.uniform3fv(uDeep, sphere.deep);
      gl.uniform3fv(uMid, sphere.mid);
      gl.uniform3fv(uLight, sphere.light);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    });
  }

  function frame(now) {
    pointer.x += (pointer.tx - pointer.x) * 0.045;
    pointer.y += (pointer.ty - pointer.y) * 0.045;
    // ~40fps cap keeps the machine cool while still looking alive.
    if (!lastFrame || now - lastFrame > 24) {
      draw(now);
      lastFrame = now;
    }
    raf = window.requestAnimationFrame(frame);
  }

  let raf = null;
  function start() {
    if (raf || reduceMotion.matches) return;
    raf = window.requestAnimationFrame(frame);
  }
  function stop() {
    if (raf) window.cancelAnimationFrame(raf);
    raf = null;
  }

  window.addEventListener("resize", resize, { passive: true });
  window.addEventListener("pointermove", (event) => {
    pointer.tx = (event.clientX / window.innerWidth) * 2 - 1;
    pointer.ty = (event.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });

  reduceMotion.addEventListener("change", () => {
    if (reduceMotion.matches) { stop(); } else { start(); }
  });

  resize();
  if (!reduceMotion.matches) start();
  else draw(performance.now());
})();
