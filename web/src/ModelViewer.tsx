import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RotateCcw } from "lucide-react";

function checkedGLB(buffer: ArrayBuffer) {
  if (buffer.byteLength < 24 || buffer.byteLength > 32 * 1024 * 1024)
    throw new Error("文件大小超出三维预览范围。");
  const view = new DataView(buffer);
  const length = view.getUint32(12, true);
  if (
    view.getUint32(0, true) !== 0x46546c67 ||
    view.getUint32(4, true) !== 2 ||
    view.getUint32(8, true) !== buffer.byteLength ||
    view.getUint32(16, true) !== 0x4e4f534a ||
    length > 4 * 1024 * 1024 ||
    length + 20 > buffer.byteLength
  )
    throw new Error("GLB 文件格式无效。");
  const spec = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buffer, 20, length)),
  );
  if (
    [...(spec.buffers ?? []), ...(spec.images ?? [])].some(
      (x) => x.uri !== undefined,
    )
  )
    throw new Error("预览需要将所有资源嵌入 GLB 文件。");
  if (
    (spec.nodes?.length ?? 0) > 5000 ||
    (spec.meshes?.length ?? 0) > 2000 ||
    (spec.accessors ?? []).reduce(
      (n: number, a: { count: number }) => n + a.count,
      0,
    ) > 12000000
  )
    throw new Error("模型复杂度超过浏览器预览上限。");
}
function disposeObject(object: THREE.Object3D) {
  const materials = new Set<THREE.Material>(),
    textures = new Set<THREE.Texture>();
  object.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    if (mesh.material)
      (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach(
        (m) => materials.add(m),
      );
  });
  for (const material of materials) {
    Object.values(material).forEach((v) => {
      if (v instanceof THREE.Texture) textures.add(v);
    });
    material.dispose();
  }
  for (const tex of textures) {
    tex.dispose();
    if (typeof ImageBitmap !== "undefined" && tex.image instanceof ImageBitmap)
      tex.image.close();
  }
}
export default function ModelViewer({ buffer }: { buffer: ArrayBuffer }) {
  const host = useRef<HTMLDivElement>(null),
    controlsRef = useRef<{
      explode: (f: number) => void;
      reset: () => void;
    } | null>(null);
  const [error, setError] = useState(""),
    [parts, setParts] = useState(0),
    [amount, setAmount] = useState(0);
  useEffect(() => {
    let cancelled = false,
      cleanup = () => {};
    setError("");
    setParts(0);
    setAmount(0);
    async function mount() {
      checkedGLB(buffer);
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url) => {
        if (!url.startsWith("blob:")) throw new Error("模型包含外部资源。");
        return url;
      });
      const { scene: model } = await new GLTFLoader(manager).parseAsync(
        buffer,
        "",
      );
      if (cancelled) {
        disposeObject(model);
        return;
      }
      const parent = host.current!;
      let renderer: THREE.WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({
          antialias: true,
          alpha: false,
          powerPreference: "low-power",
        });
      } catch {
        disposeObject(model);
        throw new Error("当前浏览器无法启用 WebGL，可下载模型到本地查看。");
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      parent.appendChild(renderer.domElement);
      renderer.domElement.setAttribute("aria-label", "三维模型画布");
      const scene = new THREE.Scene();
      scene.background = new THREE.Color("#edf2ef");
      scene.add(model);
      const lights = new THREE.HemisphereLight(0xffffff, 0x82958b, 2.6);
      scene.add(lights);
      const sun = new THREE.DirectionalLight(0xffffff, 2.1);
      sun.position.set(3, 6, 5);
      scene.add(sun);
      const camera = new THREE.PerspectiveCamera(38, 1, 0.001, 1000);
      const orbit = new OrbitControls(camera, renderer.domElement);
      orbit.enablePan = true;
      const draw = () => renderer.render(scene, camera);
      orbit.addEventListener("change", draw);
      let observer: ResizeObserver | undefined;
      cleanup = () => {
        observer?.disconnect();
        orbit.dispose();
        disposeObject(model);
        renderer.dispose();
        renderer.forceContextLoss();
        renderer.domElement.remove();
        controlsRef.current = null;
      };
      model.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(model);
      if (bounds.isEmpty()) throw new Error("模型中没有可见几何体。");
      const center = bounds.getCenter(new THREE.Vector3()),
        size = bounds.getSize(new THREE.Vector3()),
        radius = size.length() / 2 || 1;
      if (!Number.isFinite(radius)) throw new Error("模型坐标无效。");
      camera.near = radius / 1000;
      camera.far = radius * 200;
      orbit.minDistance = radius * 0.1;
      orbit.maxDistance = radius * 24;
      const isometric = new THREE.Vector3(1, 0.7, 1.25).normalize();
      const reset = () => {
        model.updateMatrixWorld(true);
        const current = new THREE.Box3()
          .setFromObject(model)
          .getBoundingSphere(new THREE.Sphere());
        const halfVertical = THREE.MathUtils.degToRad(camera.fov / 2);
        const halfNarrow = Math.atan(
          Math.tan(halfVertical) * Math.min(1, camera.aspect),
        );
        const distance =
          ((current.radius || radius) * 1.08) / Math.sin(halfNarrow);
        orbit.target.copy(current.center);
        orbit.maxDistance = Math.max(radius * 24, distance * 2);
        camera.far = Math.max(radius * 200, distance * 4);
        camera.updateProjectionMatrix();
        camera.position
          .copy(current.center)
          .addScaledVector(isometric, distance);
        orbit.update();
        draw();
      };
      const meshes: THREE.Mesh[] = [];
      model.traverse((n) => {
        if ((n as THREE.Mesh).isMesh) meshes.push(n as THREE.Mesh);
      });
      const offsets = meshes.map((mesh, index) => {
        const pos = new THREE.Box3()
          .setFromObject(mesh)
          .getCenter(new THREE.Vector3());
        const direction = pos.clone().sub(center);
        if (direction.length() < radius * 0.08)
          direction.set(Math.cos(index * 2.4), 0.45, Math.sin(index * 2.4));
        direction
          .normalize()
          .multiplyScalar(
            radius * (0.55 + (0.65 * (index + 1)) / meshes.length),
          );
        const world = mesh.getWorldPosition(new THREE.Vector3());
        const offset = mesh
          .parent!.worldToLocal(world.clone().add(direction))
          .sub(mesh.parent!.worldToLocal(world));
        return { mesh, origin: mesh.position.clone(), offset };
      });
      const resize = () => {
        const { width, height } = parent.getBoundingClientRect();
        if (!width || !height) return;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        draw();
      };
      observer = new ResizeObserver(resize);
      observer.observe(parent);
      resize();
      reset();
      controlsRef.current = {
        reset,
        explode: (f) => {
          offsets.forEach(({ mesh, origin, offset }) =>
            mesh.position.copy(origin).addScaledVector(offset, f),
          );
          draw();
        },
      };
      setParts(meshes.length);
    }
    mount().catch((e) => {
      cleanup();
      cleanup = () => {};
      if (!cancelled)
        setError(e instanceof Error ? e.message : "模型预览失败。");
    });
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [buffer]);
  return (
    <div className="model-viewer">
      <div className="model-stage" ref={host}>
        {error ? <p role="alert">{error}</p> : !parts && <p>正在加载模型…</p>}
      </div>
      {parts > 0 && (
        <div className="model-controls">
          <div>
            <span>{parts} 个网格</span>
            <button
              className="text-button"
              onClick={() => controlsRef.current?.reset()}
            >
              <RotateCcw size={14} />
              重置视角
            </button>
          </div>
          {parts > 1 && (
            <label>
              爆炸视图
              <input
                aria-label="爆炸视图"
                type="range"
                min="0"
                max="100"
                value={amount}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setAmount(v);
                  controlsRef.current?.explode(v / 100);
                }}
              />
              <output>{amount}%</output>
            </label>
          )}
          <small>拖动旋转 · 双指或滚轮缩放</small>
        </div>
      )}
    </div>
  );
}
