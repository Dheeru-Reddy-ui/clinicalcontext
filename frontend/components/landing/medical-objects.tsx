"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";

/**
 * The medical still life on the landing page, built from geometry rather
 * than downloaded models: nothing to fetch (the Content-Security-Policy
 * allows no third-party origins), nothing to license, a few kilobytes of
 * code instead of megabytes of glTF.
 *
 * Every object is lit by the studio in hero-scene.tsx; the materials here
 * only decide how each surface answers the light — glossy gelatin for the
 * capsules, chalky coating for the tablets, pearl for the cross, polished
 * chrome for the stethoscope.
 *
 * Deliberately not a red cross: the red cross on white is a protected
 * emblem under the Geneva Conventions, not a generic medical sign.
 */

export const PALETTE = {
  pearl: "#f5f7ff",
  indigo: "#6366f1",
  violet: "#8b5cf6",
  teal: "#14b8a6",
  cyan: "#22d3ee",
  sky: "#38bdf8",
  coral: "#fb7185",
} as const;

/* ------------------------------------------------------------------ shapes */

/** A polygon with every corner rounded by `radius` (convex or concave). */
function roundedPolygon(points: THREE.Vector2[], radius: number): THREE.Shape {
  const shape = new THREE.Shape();
  const n = points.length;
  for (let i = 0; i < n; i++) {
    const prev = points[(i - 1 + n) % n] as THREE.Vector2;
    const corner = points[i] as THREE.Vector2;
    const next = points[(i + 1) % n] as THREE.Vector2;
    const toPrev = prev.clone().sub(corner).normalize();
    const toNext = next.clone().sub(corner).normalize();
    const start = corner.clone().add(toPrev.multiplyScalar(radius));
    const end = corner.clone().add(toNext.multiplyScalar(radius));
    if (i === 0) shape.moveTo(start.x, start.y);
    else shape.lineTo(start.x, start.y);
    shape.quadraticCurveTo(corner.x, corner.y, end.x, end.y);
  }
  shape.closePath();
  return shape;
}

/** The outline of a plus sign: arms `arm` wide, `span` from tip to tip. */
export function crossShape(arm: number, span: number, radius: number): THREE.Shape {
  const a = arm / 2;
  const b = span / 2;
  const outline: Array<[number, number]> = [
    [a, b], [a, a], [b, a], [b, -a], [a, -a], [a, -b],
    [-a, -b], [-a, -a], [-b, -a], [-b, a], [-a, a], [-a, b],
  ];
  return roundedPolygon(
    outline.map(([x, y]) => new THREE.Vector2(x, y)),
    radius,
  );
}

/** Half a capsule shell as a lathe profile: dome at the top, open at y = 0. */
function halfCapsuleProfile(radius: number, length: number, from = 0): THREE.Vector2[] {
  const points: THREE.Vector2[] = [];
  const steps = 14;
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * (Math.PI / 2);
    points.push(new THREE.Vector2(Math.max(1e-4, Math.sin(t) * radius), length + Math.cos(t) * radius));
  }
  points.push(new THREE.Vector2(radius, from));
  return points;
}

/** A biconvex tablet: shallow domes top and bottom, a rounded rim. */
function tabletProfile(radius: number, rim: number, dome: number): THREE.Vector2[] {
  const points: THREE.Vector2[] = [];
  const domeSteps = 10;
  const edge = rim / 2;
  // Top dome, from the centre out to where the rim begins.
  for (let i = 0; i <= domeSteps; i++) {
    const t = i / domeSteps;
    const r = t * (radius - edge);
    points.push(new THREE.Vector2(Math.max(1e-4, r), edge + dome * (1 - t * t)));
  }
  // The rounded rim: a half circle around the edge.
  const rimSteps = 10;
  for (let i = 1; i < rimSteps; i++) {
    const t = Math.PI / 2 - (i / rimSteps) * Math.PI;
    points.push(new THREE.Vector2(radius - edge + Math.cos(t) * edge, Math.sin(t) * edge));
  }
  // The bottom dome, mirrored.
  for (let i = domeSteps; i >= 0; i--) {
    const t = i / domeSteps;
    const r = t * (radius - edge);
    points.push(new THREE.Vector2(Math.max(1e-4, r), -edge - dome * (1 - t * t)));
  }
  return points;
}

/* --------------------------------------------------------------- materials */

/** Materials shared by every object of a kind, disposed with the scene. */
export function useMedicalMaterials() {
  const materials = useMemo(
    () => ({
      pearl: new THREE.MeshPhysicalMaterial({
        color: PALETTE.pearl,
        roughness: 0.16,
        metalness: 0,
        clearcoat: 1,
        clearcoatRoughness: 0.06,
        iridescence: 0.45,
        iridescenceIOR: 1.35,
        iridescenceThicknessRange: [180, 520],
        sheen: 0.5,
        sheenRoughness: 0.4,
        sheenColor: new THREE.Color("#c7d2fe"),
        // A breath of self-light so the shaded side stays pearl, never grey.
        emissive: new THREE.Color("#e0e7ff"),
        emissiveIntensity: 0.06,
      }),
      gelatinWhite: new THREE.MeshPhysicalMaterial({
        color: "#f8fafc",
        roughness: 0.14,
        clearcoat: 1,
        clearcoatRoughness: 0.05,
      }),
      gelatin: (color: string) =>
        new THREE.MeshPhysicalMaterial({
          color,
          roughness: 0.12,
          clearcoat: 1,
          clearcoatRoughness: 0.04,
          sheen: 0.3,
          sheenColor: new THREE.Color("#ffffff"),
        }),
      chalk: new THREE.MeshPhysicalMaterial({
        color: "#eef2ff",
        roughness: 0.62,
        clearcoat: 0.25,
        clearcoatRoughness: 0.5,
        sheen: 0.6,
        sheenRoughness: 0.8,
        sheenColor: new THREE.Color("#e0e7ff"),
      }),
      chrome: new THREE.MeshPhysicalMaterial({
        color: "#e2e8f0",
        metalness: 1,
        roughness: 0.12,
      }),
      tubing: new THREE.MeshPhysicalMaterial({
        color: PALETTE.teal,
        roughness: 0.38,
        clearcoat: 0.7,
        clearcoatRoughness: 0.2,
      }),
      rubber: new THREE.MeshPhysicalMaterial({ color: "#1e1b4b", roughness: 0.5, clearcoat: 0.4 }),
      diaphragm: new THREE.MeshPhysicalMaterial({ color: "#0f172a", roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.15 }),
    }),
    [],
  );
  useEffect(
    () => () => {
      for (const value of Object.values(materials)) {
        if (value instanceof THREE.Material) value.dispose();
      }
    },
    [materials],
  );
  return materials;
}

export type MedicalMaterials = ReturnType<typeof useMedicalMaterials>;

/* ---------------------------------------------------------------- motion */

export interface FloatProps {
  children: ReactNode;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: number;
  /** Height of the bob, in world units. */
  amplitude?: number;
  /** Radians per second of the bob. */
  speed?: number;
  /** A slow tumble, radians per second on each axis. */
  spin?: [number, number, number];
  phase?: number;
  animate: boolean;
}

/** Floats its children: a gentle bob and an optional slow tumble. */
export function Float({
  children,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
  scale = 1,
  amplitude = 0.12,
  speed = 0.9,
  spin = [0, 0, 0],
  phase = 0,
  animate,
}: FloatProps) {
  const ref = useRef<THREE.Group>(null);
  useFrame((state, delta) => {
    const group = ref.current;
    if (!group || !animate) return;
    const t = state.clock.elapsedTime * speed + phase;
    group.position.y = position[1] + Math.sin(t) * amplitude;
    group.rotation.x += spin[0] * delta;
    group.rotation.y += spin[1] * delta;
    group.rotation.z += spin[2] * delta;
  });
  return (
    <group ref={ref} position={position} rotation={rotation} scale={scale}>
      {children}
    </group>
  );
}

/* --------------------------------------------------------------- objects */

/** The centrepiece: a pearl medical cross with a soft bevel. */
export function MedicalCross({ material }: { material: THREE.Material }) {
  const geometry = useMemo(() => {
    const g = new THREE.ExtrudeGeometry(crossShape(0.92, 2.7, 0.2), {
      depth: 0.5,
      bevelEnabled: true,
      bevelThickness: 0.16,
      bevelSize: 0.13,
      bevelSegments: 10,
      curveSegments: 18,
    });
    g.center();
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <mesh geometry={geometry} material={material} />;
}

/** A two-piece capsule: a coloured cap over a white body. */
export function Capsule({
  materials,
  color,
  radius = 0.26,
}: {
  materials: MedicalMaterials;
  color: string;
  radius?: number;
}) {
  const body = useMemo(() => new THREE.LatheGeometry(halfCapsuleProfile(radius, 0.42, 0.02), 40), [radius]);
  // The cap is a touch wider and overlaps the body, as a real capsule's does.
  const cap = useMemo(
    () => new THREE.LatheGeometry(halfCapsuleProfile(radius * 1.045, 0.42, -0.1), 40),
    [radius],
  );
  const capMaterial = useMemo(() => materials.gelatin(color), [materials, color]);
  useEffect(
    () => () => {
      body.dispose();
      cap.dispose();
      capMaterial.dispose();
    },
    [body, cap, capMaterial],
  );
  return (
    <group>
      <mesh geometry={body} material={materials.gelatinWhite} rotation={[Math.PI, 0, 0]} />
      <mesh geometry={cap} material={capMaterial} />
    </group>
  );
}

/** A round, film-coated tablet. */
export function Tablet({ material, radius = 0.36 }: { material: THREE.Material; radius?: number }) {
  const geometry = useMemo(() => new THREE.LatheGeometry(tabletProfile(radius, 0.12, 0.06), 48), [radius]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <mesh geometry={geometry} material={material} />;
}

class HelixCurve extends THREE.Curve<THREE.Vector3> {
  constructor(
    private readonly radius: number,
    private readonly height: number,
    private readonly turns: number,
    private readonly offset: number,
  ) {
    super();
  }
  override getPoint(t: number, target = new THREE.Vector3()): THREE.Vector3 {
    const angle = t * this.turns * Math.PI * 2 + this.offset;
    return target.set(Math.cos(angle) * this.radius, (t - 0.5) * this.height, Math.sin(angle) * this.radius);
  }
}

/** A DNA double helix: two backbones, bases along each, rungs between. */
export function DnaHelix({ animate }: { animate: boolean }) {
  const RADIUS = 0.55;
  const HEIGHT = 4.6;
  const TURNS = 2.2;
  const PAIRS = 26;
  const group = useRef<THREE.Group>(null);

  const parts = useMemo(() => {
    const backboneA = new THREE.TubeGeometry(new HelixCurve(RADIUS, HEIGHT, TURNS, 0), 220, 0.045, 10, false);
    const backboneB = new THREE.TubeGeometry(new HelixCurve(RADIUS, HEIGHT, TURNS, Math.PI), 220, 0.045, 10, false);
    const base = new THREE.SphereGeometry(0.1, 20, 14);
    const rung = new THREE.CylinderGeometry(0.028, 0.028, 1, 8);
    const strandA = new THREE.MeshPhysicalMaterial({
      color: PALETTE.cyan,
      roughness: 0.2,
      clearcoat: 1,
      emissive: new THREE.Color(PALETTE.teal),
      emissiveIntensity: 0.25,
    });
    const strandB = new THREE.MeshPhysicalMaterial({
      color: PALETTE.violet,
      roughness: 0.2,
      clearcoat: 1,
      emissive: new THREE.Color(PALETTE.indigo),
      emissiveIntensity: 0.25,
    });
    const rungMaterial = new THREE.MeshPhysicalMaterial({ color: "#e0e7ff", roughness: 0.3, transparent: true, opacity: 0.75 });
    return { backboneA, backboneB, base, rung, strandA, strandB, rungMaterial };
  }, []);

  const basesA = useRef<THREE.InstancedMesh>(null);
  const basesB = useRef<THREE.InstancedMesh>(null);
  const rungs = useRef<THREE.InstancedMesh>(null);

  useEffect(() => {
    const a = new HelixCurve(RADIUS, HEIGHT, TURNS, 0);
    const b = new HelixCurve(RADIUS, HEIGHT, TURNS, Math.PI);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);
    for (let i = 0; i < PAIRS; i++) {
      const t = (i + 0.5) / PAIRS;
      const pa = a.getPoint(t);
      const pb = b.getPoint(t);
      basesA.current?.setMatrixAt(i, m.makeTranslation(pa.x, pa.y, pa.z));
      basesB.current?.setMatrixAt(i, m.makeTranslation(pb.x, pb.y, pb.z));
      const mid = pa.clone().add(pb).multiplyScalar(0.5);
      const direction = pb.clone().sub(pa);
      q.setFromUnitVectors(up, direction.clone().normalize());
      m.compose(mid, q, new THREE.Vector3(1, direction.length(), 1));
      rungs.current?.setMatrixAt(i, m);
    }
    for (const mesh of [basesA.current, basesB.current, rungs.current]) {
      if (mesh) mesh.instanceMatrix.needsUpdate = true;
    }
  }, []);

  useEffect(
    () => () => {
      for (const value of Object.values(parts)) value.dispose();
    },
    [parts],
  );

  useFrame((_, delta) => {
    if (animate && group.current) group.current.rotation.y += delta * 0.35;
  });

  return (
    <group ref={group}>
      <mesh geometry={parts.backboneA} material={parts.strandA} />
      <mesh geometry={parts.backboneB} material={parts.strandB} />
      <instancedMesh ref={basesA} args={[parts.base, parts.strandA, PAIRS]} />
      <instancedMesh ref={basesB} args={[parts.base, parts.strandB, PAIRS]} />
      <instancedMesh ref={rungs} args={[parts.rung, parts.rungMaterial, PAIRS]} />
    </group>
  );
}

/** A stethoscope: chrome chest piece, teal tubing, chrome binaurals, ear tips. */
export function Stethoscope({ materials }: { materials: MedicalMaterials }) {
  const parts = useMemo(() => {
    const tubingCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0.12, 0),
      new THREE.Vector3(0.05, 0.9, 0.15),
      new THREE.Vector3(-0.55, 1.7, 0.3),
      new THREE.Vector3(-0.2, 2.45, 0.1),
      new THREE.Vector3(0.35, 2.95, 0),
      new THREE.Vector3(0.4, 3.45, 0),
    ]);
    const junction = new THREE.Vector3(0.4, 3.45, 0);
    const binaural = (side: 1 | -1) =>
      new THREE.CatmullRomCurve3([
        junction.clone(),
        new THREE.Vector3(0.4 + side * 0.28, 3.85, side * 0.05),
        new THREE.Vector3(0.4 + side * 0.52, 4.5, 0.05),
        new THREE.Vector3(0.4 + side * 0.44, 5.05, 0.18),
        new THREE.Vector3(0.4 + side * 0.24, 5.3, 0.3),
      ]);
    const left = binaural(-1);
    const right = binaural(1);
    return {
      tubing: new THREE.TubeGeometry(tubingCurve, 160, 0.075, 14, false),
      left: new THREE.TubeGeometry(left, 90, 0.04, 10, false),
      right: new THREE.TubeGeometry(right, 90, 0.04, 10, false),
      leftTip: left.getPoint(1),
      rightTip: right.getPoint(1),
      junction,
      bell: new THREE.CylinderGeometry(0.46, 0.5, 0.18, 64),
      rim: new THREE.TorusGeometry(0.47, 0.05, 16, 64),
      face: new THREE.CircleGeometry(0.41, 64),
      stem: new THREE.CylinderGeometry(0.06, 0.07, 0.34, 20),
      knuckle: new THREE.SphereGeometry(0.1, 24, 16),
      earTip: new THREE.SphereGeometry(0.1, 24, 16),
    };
  }, []);

  useEffect(
    () => () => {
      for (const value of Object.values(parts)) {
        if (value instanceof THREE.BufferGeometry) value.dispose();
      }
    },
    [parts],
  );

  return (
    <group>
      {/* Chest piece, facing the camera. */}
      <group position={[0, -0.28, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <mesh geometry={parts.bell} material={materials.chrome} />
        <mesh geometry={parts.rim} material={materials.chrome} rotation={[Math.PI / 2, 0, 0]} position={[0, 0.09, 0]} />
        <mesh geometry={parts.face} material={materials.diaphragm} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.095, 0]} />
      </group>
      <mesh geometry={parts.stem} material={materials.chrome} position={[0, 0.02, 0]} />
      <mesh geometry={parts.tubing} material={materials.tubing} />
      <mesh geometry={parts.knuckle} material={materials.chrome} position={parts.junction} />
      <mesh geometry={parts.left} material={materials.chrome} />
      <mesh geometry={parts.right} material={materials.chrome} />
      <mesh geometry={parts.earTip} material={materials.rubber} position={parts.leftTip} />
      <mesh geometry={parts.earTip} material={materials.rubber} position={parts.rightTip} />
    </group>
  );
}

/** A soft round glow, drawn once into a canvas: bloom without a post pass. */
export function useGlowTexture(): THREE.Texture {
  const texture = useMemo(() => {
    const size = 256;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const context = canvas.getContext("2d");
    if (context) {
      const gradient = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      gradient.addColorStop(0, "rgba(255,255,255,1)");
      gradient.addColorStop(0.25, "rgba(255,255,255,0.45)");
      gradient.addColorStop(0.6, "rgba(255,255,255,0.08)");
      gradient.addColorStop(1, "rgba(255,255,255,0)");
      context.fillStyle = gradient;
      context.fillRect(0, 0, size, size);
    }
    const t = new THREE.CanvasTexture(canvas);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }, []);
  useEffect(() => () => texture.dispose(), [texture]);
  return texture;
}

/** Small plus signs drifting far behind everything, like out-of-focus light. */
export function CrossBokeh({ count, spread, animate }: { count: number; spread: [number, number]; animate: boolean }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const geometry = useMemo(() => new THREE.ShapeGeometry(crossShape(0.34, 1, 0.08), 6), []);
  const material = useMemo(
    () =>
      new THREE.MeshBasicMaterial({
        color: "#c7d2fe",
        transparent: true,
        opacity: 0.14,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    [],
  );
  const seeds = useMemo(() => {
    // Deterministic scatter so the composition is the same on every visit.
    let s = 7;
    const random = () => {
      s = (s * 16807) % 2147483647;
      return (s - 1) / 2147483646;
    };
    return Array.from({ length: count }, () => ({
      x: (random() - 0.5) * spread[0],
      y: (random() - 0.5) * spread[1],
      z: -4 - random() * 6,
      scale: 0.12 + random() * 0.28,
      speed: 0.05 + random() * 0.12,
      tilt: random() * Math.PI,
    }));
  }, [count, spread]);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  const matrix = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion(), []);
  const e = useMemo(() => new THREE.Euler(), []);
  const v = useMemo(() => new THREE.Vector3(), []);
  const sc = useMemo(() => new THREE.Vector3(), []);

  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    const t = animate ? state.clock.elapsedTime : 0;
    seeds.forEach((seed, i) => {
      const y = ((seed.y + t * seed.speed + spread[1] / 2) % spread[1]) - spread[1] / 2;
      v.set(seed.x, y, seed.z);
      e.set(0, 0, seed.tilt + t * seed.speed);
      q.setFromEuler(e);
      sc.setScalar(seed.scale);
      m.setMatrixAt(i, matrix.compose(v, q, sc));
    });
    m.instanceMatrix.needsUpdate = true;
  });

  return <instancedMesh ref={mesh} args={[geometry, material, count]} frustumCulled={false} />;
}
