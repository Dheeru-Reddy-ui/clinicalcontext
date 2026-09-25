"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";

import {
  Capsule,
  CrossBokeh,
  DnaHelix,
  Float,
  MedicalCross,
  PALETTE,
  Stethoscope,
  Tablet,
  useGlowTexture,
  useMedicalMaterials,
} from "@/components/landing/medical-objects";

/**
 * The landing page's 3D hero: a medical still life under a proper studio
 * rig, rendered behind the headline and the glass cards.
 *
 * Lighting, which is most of what makes it read as "real":
 *  - image-based light from a studio built in code — softboxes, two coloured
 *    strip lights and a ring light rendered into an environment map — so
 *    every glossy surface carries clean, believable reflections;
 *  - a warm key light from above right, cyan and violet rim lights from
 *    behind to separate each object from the navy background, a cool
 *    hemisphere fill, and an indigo bounce from below the centrepiece;
 *  - Khronos PBR Neutral tone mapping, which keeps white white and brand
 *    colours true instead of washing them toward grey.
 *
 * Nothing is fetched: no HDR files, no models, no fonts.
 */

export interface HeroSceneProps {
  /** Where the centrepiece sits, as a fraction of the canvas (0..1 from top-left). */
  anchor: { x: number; y: number };
  /** Animate (false under prefers-reduced-motion: one still frame). */
  animate: boolean;
  /** Render at all (false while the hero is scrolled out of view). */
  active: boolean;
  /** The element whose scroll position drives the gentle scroll parallax. */
  scrollRoot: RefObject<HTMLElement | null>;
  onReady: () => void;
}

/** A studio for reflections: softboxes and strip lights around a dark room. */
function StudioEnvironment() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const invalidate = useThree((s) => s.invalidate);

  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const studio = new THREE.Scene();
    studio.background = new THREE.Color("#050817");
    const disposables: Array<{ dispose: () => void }> = [];

    const panel = (
      size: [number, number],
      color: string,
      intensity: number,
      position: [number, number, number],
    ) => {
      const geometry = new THREE.PlaneGeometry(size[0], size[1]);
      const material = new THREE.MeshBasicMaterial({
        color: new THREE.Color(color).multiplyScalar(intensity),
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...position);
      mesh.lookAt(0, 0, 0);
      studio.add(mesh);
      disposables.push(geometry, material);
    };

    // Key softbox overhead-front, a large faint front fill, coloured strips
    // either side that echo the page's indigo and teal, and a floor bounce.
    panel([9, 3.5], "#ffffff", 4.2, [1.5, 7, 5]);
    panel([6, 5], "#ffffff", 2.4, [-4.5, 3.5, 8.5]);
    panel([14, 6], "#dbe4ff", 0.8, [0, 0.5, 9]);
    panel([1.6, 11], PALETTE.indigo, 3.4, [-8, 0.5, 1.5]);
    panel([1.6, 11], PALETTE.teal, 3, [8, 0.5, 1.5]);
    panel([10, 2], "#a5b4fc", 1.1, [0, -6, 3]);

    // A ring light behind the camera: the round highlight in the pearl.
    const ringGeometry = new THREE.TorusGeometry(2.2, 0.35, 16, 64);
    const ringMaterial = new THREE.MeshBasicMaterial({ color: new THREE.Color("#ffffff").multiplyScalar(2.2) });
    const ring = new THREE.Mesh(ringGeometry, ringMaterial);
    ring.position.set(-2, 2, 10);
    ring.lookAt(0, 0, 0);
    studio.add(ring);
    disposables.push(ringGeometry, ringMaterial);

    const target = pmrem.fromScene(studio, 0.035);
    scene.environment = target.texture;
    scene.environmentIntensity = 1.15;
    invalidate();

    return () => {
      scene.environment = null;
      target.dispose();
      pmrem.dispose();
      for (const d of disposables) d.dispose();
    };
  }, [gl, scene, invalidate]);

  return null;
}

/** Drops the pixel ratio once if the first second of frames runs slow. */
function AdaptiveQuality() {
  const setDpr = useThree((s) => s.setDpr);
  const samples = useRef({ frames: 0, time: 0, done: false });
  useFrame((_, delta) => {
    const s = samples.current;
    if (s.done) return;
    s.frames += 1;
    s.time += delta;
    if (s.frames >= 75) {
      s.done = true;
      if (s.time / s.frames > 1 / 40) setDpr(1);
    }
  });
  return null;
}

/** Calls onReady after the first frame is on screen. */
function FirstFrame({ onReady }: { onReady: () => void }) {
  const fired = useRef(false);
  useFrame(() => {
    if (fired.current) return;
    fired.current = true;
    requestAnimationFrame(() => onReady());
  });
  return null;
}

const easeOutCubic = (t: number) => 1 - (1 - Math.min(1, Math.max(0, t))) ** 3;

function Composition({ anchor, animate, scrollRoot }: Omit<HeroSceneProps, "active" | "onReady">) {
  const viewport = useThree((s) => s.viewport);
  const materials = useMedicalMaterials();
  const glow = useGlowTexture();

  const rig = useRef<THREE.Group>(null);
  const cross = useRef<THREE.Group>(null);
  const orbit = useRef<THREE.Group>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const scroll = useRef(0);
  const born = useRef<number | null>(null);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      pointer.current.x = (e.clientX / window.innerWidth) * 2 - 1;
      pointer.current.y = (e.clientY / window.innerHeight) * 2 - 1;
    };
    const onScroll = () => {
      const root = scrollRoot.current;
      const height = root?.offsetHeight ?? window.innerHeight;
      scroll.current = Math.min(1, Math.max(0, window.scrollY / Math.max(1, height)));
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("scroll", onScroll);
    };
  }, [scrollRoot]);

  // Layout in world units, derived from the canvas so it holds on any screen.
  const vw = viewport.width;
  const vh = viewport.height;
  const narrow = vw < 8.5;
  // The ring (about 3.9 units across) takes at most ~80% of a phone's width.
  const scale = Math.min(0.8, Math.max(0.36, vw * 0.2));
  const center: [number, number, number] = [(anchor.x - 0.5) * vw, (0.5 - anchor.y) * vh, 0];
  /** A point on the canvas, as fractions from its top-left, in world units. */
  const at = (fx: number, fy: number, z: number): [number, number, number] => [(fx - 0.5) * vw, (0.5 - fy) * vh, z];

  useFrame((state, delta) => {
    const group = rig.current;
    if (!group) return;
    if (born.current === null) born.current = state.clock.elapsedTime;
    const age = animate ? state.clock.elapsedTime - born.current : 10;
    const intro = easeOutCubic(age / 1.6);

    // Follow the pointer a little: the scene turns towards the reader.
    const k = animate ? 1 - Math.exp(-delta * 3) : 1;
    const targetY = animate ? pointer.current.x * 0.14 : 0;
    const targetX = animate ? pointer.current.y * 0.07 : 0;
    group.rotation.y += (targetY - group.rotation.y) * k;
    group.rotation.x += (targetX - group.rotation.x) * k;
    // Scrolling, the scene drifts down a little against the page: it lags,
    // as something further away would, and never slides up over the text.
    group.position.y = (1 - intro) * -0.8 - scroll.current * vh * 0.1;
    group.scale.setScalar(0.88 + 0.12 * intro);

    if (cross.current) {
      const t = animate ? state.clock.elapsedTime : 0;
      cross.current.rotation.y = Math.sin(t * 0.45) * 0.38;
      cross.current.rotation.x = Math.sin(t * 0.3 + 1) * 0.06;
    }
    if (orbit.current && animate) orbit.current.rotation.z += delta * 0.16;
  });

  const orbitPills = useMemo(
    () => [
      { angle: 0.35, kind: "capsule" as const, color: PALETTE.indigo, spin: [0.5, 0.8, 0.2] as [number, number, number] },
      { angle: 1.55, kind: "tablet" as const, color: "", spin: [0.6, 0.3, 0.4] as [number, number, number] },
      { angle: 2.7, kind: "capsule" as const, color: PALETTE.teal, spin: [0.3, 0.7, 0.5] as [number, number, number] },
      { angle: 3.85, kind: "capsule" as const, color: PALETTE.coral, spin: [0.7, 0.4, 0.3] as [number, number, number] },
      { angle: 5.05, kind: "tablet" as const, color: "", spin: [0.4, 0.6, 0.2] as [number, number, number] },
    ],
    [],
  );
  const ORBIT_RADIUS = 2.75;

  return (
    <group ref={rig}>
      <CrossBokeh count={narrow ? 14 : 28} spread={[vw * 1.3, vh * 1.3]} animate={animate} />

      {/* The centrepiece: glow, ring light, pearl cross, orbiting medicines. */}
      <group position={center} scale={scale}>
        <sprite scale={[8.5, 8.5, 1]} position={[0, 0, -2.4]}>
          <spriteMaterial map={glow} color={PALETTE.indigo} transparent opacity={0.55} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
        </sprite>
        <sprite scale={[4.2, 4.2, 1]} position={[0.3, -0.2, -2]}>
          <spriteMaterial map={glow} color={PALETTE.cyan} transparent opacity={0.35} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
        </sprite>

        {/* A ring light standing behind the cross. */}
        <mesh position={[0, 0, -1.3]}>
          <torusGeometry args={[1.95, 0.022, 16, 160]} />
          <meshBasicMaterial color="#c7d2fe" transparent opacity={0.9} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
        <mesh position={[0, 0, -1.32]}>
          <torusGeometry args={[1.95, 0.12, 16, 160]} />
          <meshBasicMaterial color={PALETTE.indigo} transparent opacity={0.18} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>

        <group ref={cross}>
          <Float animate={animate} amplitude={0.1} speed={0.8}>
            <MedicalCross material={materials.pearl} />
          </Float>
        </group>

        {/* A tilted orbit carrying capsules and tablets in front of and behind the cross. */}
        <group rotation={[1.2, 0, -0.18]}>
          <mesh>
            <torusGeometry args={[ORBIT_RADIUS, 0.006, 8, 220]} />
            <meshBasicMaterial color="#a5b4fc" transparent opacity={0.35} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} />
          </mesh>
          <group ref={orbit}>
            {orbitPills.map((pill) => (
              <group
                key={pill.angle}
                position={[Math.cos(pill.angle) * ORBIT_RADIUS, Math.sin(pill.angle) * ORBIT_RADIUS, 0]}
                rotation={[-1.2, 0, 0]}
              >
                <Float animate={animate} amplitude={0.05} speed={1.2} phase={pill.angle} spin={pill.spin} rotation={[pill.angle, pill.angle * 0.5, 0.4]} scale={0.85}>
                  {pill.kind === "capsule" ? (
                    <Capsule materials={materials} color={pill.color} />
                  ) : (
                    <Tablet material={materials.chalk} />
                  )}
                </Float>
              </group>
            ))}
          </group>
        </group>
      </group>

      {/* The sides: DNA on the left, a stethoscope on the right, pills up by the headline. */}
      {!narrow && (
        <>
          <Float animate={animate} position={at(0.07, 0.62, -1.6)} rotation={[0, 0, 0.3]} scale={0.78} amplitude={0.14} speed={0.6}>
            <DnaHelix animate={animate} />
          </Float>

          <Float
            animate={animate}
            position={at(0.94, 0.83, -0.8)}
            rotation={[0.2, -0.45, 0.5]}
            scale={0.52}
            amplitude={0.12}
            speed={0.7}
            phase={1.4}
          >
            <Stethoscope materials={materials} />
          </Float>

          <Float
            animate={animate}
            position={at(0.12, 0.2, -1.2)}
            rotation={[0.6, 0.4, 0.9]}
            scale={0.72}
            spin={[0.12, 0.22, 0]}
            phase={2}
          >
            <Capsule materials={materials} color={PALETTE.violet} />
          </Float>
          <Float
            animate={animate}
            position={at(0.885, 0.31, -1.4)}
            rotation={[1.1, 0.2, 0.3]}
            scale={0.7}
            spin={[0.15, 0.1, 0.12]}
            phase={3.1}
          >
            <Tablet material={materials.chalk} />
          </Float>
          <Float
            animate={animate}
            position={at(0.855, 0.085, -2.6)}
            rotation={[0.3, 0.9, -0.8]}
            scale={0.5}
            spin={[0.2, 0.18, 0]}
            phase={4.2}
          >
            <Capsule materials={materials} color={PALETTE.sky} />
          </Float>
        </>
      )}

      {/* Lights. */}
      <hemisphereLight args={["#dbe4ff", "#1e1b4b", 0.9]} />
      {/* Key: high, left and in front — the light the eye reads first. */}
      <directionalLight position={[-4, 6, 9]} intensity={5.2} color="#ffffff" />
      {/* Fill: low and to the right, cool, a third of the key. */}
      <directionalLight position={[6, -2, 8]} intensity={1.7} color="#c7d2fe" />
      {/* Rims from behind, in the page's teal and violet, to cut each object out of the night. */}
      <directionalLight position={[-7, 3, -6]} intensity={4} color="#5eead4" />
      <directionalLight position={[7, 2, -6]} intensity={3.4} color="#a78bfa" />
      <pointLight position={[center[0], center[1] - 2.4, 2.6]} intensity={12} distance={9} decay={2} color="#818cf8" />
    </group>
  );
}

export default function HeroScene({ anchor, animate, active, scrollRoot, onReady }: HeroSceneProps) {
  return (
    <Canvas
      className="!pointer-events-none"
      aria-hidden
      dpr={[1, 1.75]}
      frameloop={!active ? "never" : animate ? "always" : "demand"}
      camera={{ position: [0, 0, 14], fov: 30, near: 0.1, far: 60 }}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance", stencil: false }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.NeutralToneMapping;
        gl.toneMappingExposure = 1;
        gl.setClearColor(0x000000, 0);
      }}
    >
      <StudioEnvironment />
      <Composition anchor={anchor} animate={animate} scrollRoot={scrollRoot} />
      <FirstFrame onReady={onReady} />
      {animate && <AdaptiveQuality />}
    </Canvas>
  );
}
