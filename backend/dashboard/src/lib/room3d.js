import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import { REGION_TYPES } from './format';

/**
 * A scanned room in three.js, in room coordinates: meters, Y up, floor at y = 0.
 * The GLB is loaded as exported and placed with the room's `mesh_to_room` matrix
 * (glTF and the room frame share axes, so no other conversion is needed).
 *
 * Orbit mode is for tagging and clicking points; `lookThrough(registration)` pins the
 * view to a registered camera so the scan can be laid over that camera's photo.
 */
export class RoomScene {
  constructor(container) {
    this.container = container;
    // Always clear to transparent: the page's own background shows behind the scan (and the
    // camera photo behind an overlay).
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0x000000, 0);
    container.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'room-labels';
    container.appendChild(this.labels.domElement);

    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 2.2));
    this.root = new THREE.Group();
    this.root.matrixAutoUpdate = false;
    this.scene.add(this.root);
    this.regionGroup = new THREE.Group();
    this.markerGroup = new THREE.Group();
    this.extraGroup = new THREE.Group();
    this.rebuiltGroup = new THREE.Group();
    this.scene.add(this.regionGroup, this.markerGroup, this.extraGroup, this.rebuiltGroup);
    this.walls = [];

    this.camera = new THREE.PerspectiveCamera(50, 1, 0.02, 200);
    this.camera.position.set(4, 4, 4);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener('change', () => this.render());
    this.raycaster = new THREE.Raycaster();
    this.meshes = [];
    this.fixedAspect = null;

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.labels.setSize(w, h);
    this.camera.aspect = this.fixedAspect || w / h;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  render() {
    if (this.disposed) return;
    // Dollhouse view: from outside the room, the walls between the camera and the room open up.
    this.walls.forEach((w) => {
      w.mesh.visible = !this.controls.enabled || this.camera.position.clone().sub(w.center).dot(w.inward) > 0;
    });
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }

  async load(url, meshToRoom, opacity = 1) {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.disposed) return;
    // Scans carry their real lighting in the texture: show it unlit, as captured.
    gltf.scene.traverse((obj) => {
      if (!obj.isMesh) return;
      const old = obj.material;
      const map = old?.map || null;
      obj.material = map
        ? new THREE.MeshBasicMaterial({ map, transparent: opacity < 1, opacity })
        : new THREE.MeshLambertMaterial({
            color: old?.color || 0xbdbdbd,
            vertexColors: Boolean(obj.geometry.attributes.color),
            flatShading: !obj.geometry.attributes.normal, // as glTF asks for meshes without normals
            transparent: opacity < 1,
            opacity,
          });
      old?.dispose?.();
      this.meshes.push(obj);
    });
    this.root.matrix.fromArray(meshToRoom.flat()).transpose(); // row-major -> three's column-major
    this.root.add(gltf.scene);
    this.root.updateMatrixWorld(true);
    this.bounds = new THREE.Box3().setFromObject(this.root);
    this.render();
  }

  /**
   * Show the rebuilt room (plain colored boxes) instead of the scan, or the scan again with
   * null. Picking still hits the scan underneath, so clicked points stay measured points.
   */
  setRebuilt(rebuilt) {
    clear(this.rebuiltGroup);
    this.walls = [];
    this.root.visible = !rebuilt;
    if (rebuilt) {
      const kinds = Object.fromEntries(rebuilt.objects.map((o) => [o.id, o.kind]));
      const floor = rebuilt.objects.find((o) => o.kind === 'floor');
      const middle = new THREE.Vector3(...(floor ? floor.box.center : [0, 0, 0]));
      const sun = new THREE.DirectionalLight(0xffffff, 1.1);
      sun.position.set(3, 8, 5);
      this.rebuiltGroup.add(sun);
      rebuilt.boxes.forEach((b) => {
        const color = new THREE.Color(b.color);
        const material = b.label ? labelMaterials(b.label, b.size, color) : new THREE.MeshLambertMaterial({ color });
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(...b.size), material);
        mesh.position.set(...b.center);
        mesh.rotation.y = THREE.MathUtils.degToRad(b.yaw_deg);
        this.rebuiltGroup.add(mesh);
        if (kinds[b.object_id] === 'wall') {
          const a = mesh.rotation.y;
          const normal = new THREE.Vector3(Math.sin(a), 0, Math.cos(a)); // the wall's own Z
          const inward = middle.clone().sub(mesh.position).dot(normal) >= 0 ? normal : normal.negate();
          this.walls.push({ mesh, inward, center: mesh.position.clone() });
        }
      });
    }
    this.render();
  }

  setOpacity(opacity) {
    this.rebuiltGroup.traverse((m) => {
      (Array.isArray(m.material) ? m.material : m.material ? [m.material] : []).forEach((mat) => {
        mat.transparent = opacity < 1;
        mat.opacity = opacity;
      });
    });
    this.meshes.forEach((m) => {
      m.material.transparent = opacity < 1;
      m.material.opacity = opacity;
      m.material.needsUpdate = true;
    });
    this.render();
  }

  /** Orbit view from above one corner, looking at the middle of the room. */
  frame() {
    if (!this.bounds) return;
    const center = this.bounds.getCenter(new THREE.Vector3());
    const size = this.bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z, 2);
    center.y = Math.min(center.y, 1);
    this.camera.position.set(center.x + span * 0.55, span * 0.9, center.z + span * 0.75);
    this.controls.target.copy(center);
    this.controls.update();
    this.render();
  }

  /** Ray-pick the scan under a pointer; returns the room point and surface normal, or null. */
  pick(clientX, clientY) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObjects(this.meshes, false)[0];
    if (!hit) return null;
    const normal = hit.face
      ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld).normalize()
      : new THREE.Vector3(0, 1, 0);
    if (normal.dot(this.raycaster.ray.direction) > 0) normal.negate(); // face the viewer
    return { point: hit.point.toArray(), normal: normal.toArray() };
  }

  /** The region box under a pointer (nearest first), or null. */
  pickRegion(clientX, clientY) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const boxes = this.regionGroup.children.map((g) => g.children[0]);
    const hit = this.raycaster.intersectObjects(boxes, false)[0];
    return hit ? hit.object.parent.userData.regionId : null;
  }

  setRegions(regions, { selectedId = null, labelFor = () => '' } = {}) {
    clear(this.regionGroup);
    regions.forEach((r) => {
      const color = new THREE.Color(REGION_TYPES[r.region_type]?.color || '#888');
      const selected = r.region_id === selectedId;
      const geometry = new THREE.BoxGeometry(...r.box.size);
      const group = new THREE.Group();
      group.position.set(...r.box.center);
      group.rotation.y = THREE.MathUtils.degToRad(r.box.yaw_deg || 0);
      group.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: selected ? 0.32 : 0.16, depthWrite: false,
      })));
      group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({
        color: selected ? 0xffffff : color, depthTest: false, transparent: true, opacity: selected ? 1 : 0.9,
      })));
      const text = labelFor(r);
      if (text) group.add(label(text, color.getStyle(), [0, r.box.size[1] / 2 + 0.06, 0], selected));
      group.userData.regionId = r.region_id;
      this.regionGroup.add(group);
    });
    this.render();
  }

  /** Numbered points: [{ position: [x, y, z], text, color }]. */
  setMarkers(markers) {
    clear(this.markerGroup);
    markers.forEach((m) => {
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(m.radius || 0.035, 16, 12),
        new THREE.MeshBasicMaterial({ color: m.color || '#0a6ae0', depthTest: false, transparent: true }),
      );
      dot.renderOrder = 10;
      dot.position.set(...m.position);
      if (m.text) dot.add(label(m.text, m.color || '#0a6ae0', [0, 0.09, 0], false, 'marker'));
      this.markerGroup.add(dot);
    });
    this.render();
  }

  /** A registered camera drawn as a small frustum pointing where it looks. */
  setCameras(registrations) {
    clear(this.extraGroup);
    registrations.forEach((reg) => {
      const { R, center } = cameraPose(reg);
      const [w, h] = reg.frame_size;
      const { fx, fy, cx, cy } = reg.intrinsics;
      const depth = 0.45;
      const corners = [[0, 0], [w, 0], [w, h], [0, h]].map(([u, v]) => {
        const d = new THREE.Vector3((u - cx) / fx, (v - cy) / fy, 1).multiplyScalar(depth);
        return new THREE.Vector3(
          R[0][0] * d.x + R[1][0] * d.y + R[2][0] * d.z,
          R[0][1] * d.x + R[1][1] * d.y + R[2][1] * d.z,
          R[0][2] * d.x + R[1][2] * d.y + R[2][2] * d.z,
        ).add(center);
      });
      const pts = [];
      corners.forEach((c, i) => pts.push(center, c, c, corners[(i + 1) % 4]));
      const lines = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: 0x171717, depthTest: false, transparent: true }),
      );
      lines.add(label(reg.label || reg.layout_id, '#171717', [0, 0, 0], false, 'marker'));
      lines.children[0].position.copy(center).add(new THREE.Vector3(0, 0.12, 0));
      this.extraGroup.add(lines);
    });
    this.render();
  }

  /** Pin the view to a registered camera (for laying the scan over its photo). */
  lookThrough(reg) {
    const { R, center } = cameraPose(reg);
    const [w, h] = reg.frame_size;
    this.controls.enabled = false;
    this.camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(h / 2 / reg.intrinsics.fy));
    this.fixedAspect = w / h;
    // OpenCV camera axes are the rows of R (x right, y down, z forward); three looks down -Z with Y up.
    const x = new THREE.Vector3(...R[0]);
    const y = new THREE.Vector3(...R[1]).negate();
    const z = new THREE.Vector3(...R[2]).negate();
    this.camera.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
    this.camera.position.copy(center);
    this.camera.near = 0.05;
    this.resize();
  }

  dispose() {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.scene.traverse((obj) => {
      obj.geometry?.dispose?.();
      const mats = Array.isArray(obj.material) ? obj.material : obj.material ? [obj.material] : [];
      mats.forEach((m) => {
        m.map?.dispose?.();
        m.dispose?.();
      });
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labels.domElement.remove();
  }
}

export function cameraPose(reg) {
  const R = reg.rotation;
  const t = reg.translation;
  // center = -R^T t
  const center = new THREE.Vector3(
    -(R[0][0] * t[0] + R[1][0] * t[1] + R[2][0] * t[2]),
    -(R[0][1] * t[0] + R[1][1] * t[1] + R[2][1] * t[2]),
    -(R[0][2] * t[0] + R[1][2] * t[1] + R[2][2] * t[2]),
  );
  return { R, center };
}

function label(text, color, offset, strong, kind = 'region') {
  const el = document.createElement('span');
  el.className = `room-label room-label-${kind}${strong ? ' strong' : ''}`;
  el.textContent = text;
  el.style.setProperty('--label-color', color);
  const obj = new CSS2DObject(el);
  obj.position.set(...offset);
  return obj;
}

/** A printed shelf label: the text on the front face, plain elsewhere. */
function labelMaterials(text, size, color) {
  const canvas = document.createElement('canvas');
  const scale = 1600;
  canvas.width = Math.max(64, Math.round(size[0] * scale));
  canvas.height = Math.max(24, Math.round(size[1] * scale));
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = `#${color.getHexString()}`;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#1b1b1b';
  ctx.font = `600 ${Math.round(canvas.height * 0.5)}px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width * 0.92);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const plain = new THREE.MeshLambertMaterial({ color });
  // BoxGeometry face order: +x, -x, +y, -y, +z (front), -z.
  return [plain, plain, plain, plain, new THREE.MeshLambertMaterial({ map }), plain];
}

function clear(group) {
  [...group.children].forEach((child) => {
    child.traverse((obj) => {
      obj.geometry?.dispose?.();
      (Array.isArray(obj.material) ? obj.material : [obj.material]).forEach((m) => {
        m?.map?.dispose?.();
        m?.dispose?.();
      });
      if (obj.isCSS2DObject) obj.element.remove();
    });
    group.remove(child);
  });
}

/**
 * A box from two clicks on a surface. On a shelf front (a vertical face) the clicks are
 * opposite corners of the front and the box extends `depth` into the shelf. On a
 * counter top (a horizontal face) they are opposite corners of the top and the box
 * rises `height` above it, lined up with the room's axes.
 */
export function boxFromClicks(a, b, { depth = 0.35, height = 0.35 } = {}) {
  const normal = new THREE.Vector3(...a.normal);
  const pa = new THREE.Vector3(...a.point);
  const pb = new THREE.Vector3(...b.point);
  const round = (v) => Math.round(v * 1000) / 1000;
  if (Math.abs(normal.y) < 0.6) {
    const out = new THREE.Vector3(normal.x, 0, normal.z).normalize(); // out of the shelf
    const yaw = Math.atan2(out.x, out.z); // the box's +Z faces out
    const across = new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw)); // the box's +X
    const delta = pb.clone().sub(pa);
    const width = Math.max(0.05, Math.abs(delta.dot(across)));
    const tall = Math.max(0.05, Math.abs(delta.y));
    const front = pa.clone().add(pb).multiplyScalar(0.5);
    const center = front.sub(out.clone().multiplyScalar(depth / 2));
    return { center: center.toArray().map(round), size: [width, tall, depth].map(round), yaw_deg: round(THREE.MathUtils.radToDeg(yaw)) };
  }
  const top = Math.max(pa.y, pb.y);
  const width = Math.max(0.05, Math.abs(pb.x - pa.x));
  const deep = Math.max(0.05, Math.abs(pb.z - pa.z));
  return {
    center: [(pa.x + pb.x) / 2, top + height / 2, (pa.z + pb.z) / 2].map(round),
    size: [width, height, deep].map(round),
    yaw_deg: 0,
  };
}
