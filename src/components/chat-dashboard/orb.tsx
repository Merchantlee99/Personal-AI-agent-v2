"use client";

import { Component, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";
import type { AgentState } from "./types";
import styles from "./orb.module.css";

type OrbProps = {
  colors: [string, string];
  agentState: AgentState;
  agentId?: "minerva" | "clio" | "hermes" | "aegis";
};

type OrbProfile = {
  corePulse: number;
  coreDrift: number;
  shellSpin: number;
  shellJitter: number;
  waveSpeed: number;
  waveAmplitude: number;
  fluxSpeed: number;
  bloom: number;
};

type WebGLCapability = {
  webgl: boolean;
  webgl2: boolean;
};

type OrbErrorBoundaryProps = {
  fallback: ReactNode;
  children: ReactNode;
};

type OrbErrorBoundaryState = {
  hasError: boolean;
};

const PROFILE: Record<AgentState, OrbProfile> = {
  idle: {
    corePulse: 1.12,
    coreDrift: 0.56,
    shellSpin: 0.42,
    shellJitter: 0.12,
    waveSpeed: 0.64,
    waveAmplitude: 0.16,
    fluxSpeed: 0.86,
    bloom: 1.35,
  },
  listening: {
    corePulse: 1.58,
    coreDrift: 0.82,
    shellSpin: 0.68,
    shellJitter: 0.16,
    waveSpeed: 0.84,
    waveAmplitude: 0.2,
    fluxSpeed: 1.02,
    bloom: 1.7,
  },
  thinking: {
    corePulse: 2.36,
    coreDrift: 1.18,
    shellSpin: 1.04,
    shellJitter: 0.2,
    waveSpeed: 1.16,
    waveAmplitude: 0.24,
    fluxSpeed: 1.26,
    bloom: 2.1,
  },
  working: {
    corePulse: 2.02,
    coreDrift: 1.0,
    shellSpin: 0.92,
    shellJitter: 0.17,
    waveSpeed: 1.02,
    waveAmplitude: 0.22,
    fluxSpeed: 1.14,
    bloom: 1.95,
  },
  warning: {
    corePulse: 3.4,
    coreDrift: 1.56,
    shellSpin: 1.52,
    shellJitter: 0.28,
    waveSpeed: 1.72,
    waveAmplitude: 0.3,
    fluxSpeed: 1.72,
    bloom: 2.5,
  },
};

class OrbErrorBoundary extends Component<OrbErrorBoundaryProps, OrbErrorBoundaryState> {
  constructor(props: OrbErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): OrbErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    console.error("[orb] render failed", error);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

function detectWebGLCapability(): WebGLCapability {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return { webgl: true, webgl2: true };
  }
  try {
    const canvas = document.createElement("canvas");
    const webgl2 = canvas.getContext("webgl2");
    if (webgl2) return { webgl: true, webgl2: true };
    const webgl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
    return { webgl: Boolean(webgl), webgl2: false };
  } catch {
    return { webgl: false, webgl2: false };
  }
}

function OrbFallback() {
  return (
    <div className={styles.fallbackRoot}>
      <div className={styles.fallbackRing} />
      <div className={`${styles.fallbackRing} ${styles.fallbackInnerRing}`} />
      <div className={styles.fallbackCore} />
    </div>
  );
}

function rand(seed: number) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453123;
  return x - Math.floor(x);
}

function pulseSignal(t: number, profile: OrbProfile) {
  return 0.5 + 0.5 * Math.sin(t * profile.corePulse * 0.9);
}

function CoreCluster({
  colorA,
  colorB,
  agentState,
}: {
  colorA: THREE.Color;
  colorB: THREE.Color;
  agentState: AgentState;
}) {
  const pointsRef = useRef<THREE.Points>(null);
  const coreSphereRef = useRef<THREE.Mesh>(null);
  const count = 520;

  const data = useMemo(() => {
    const dirs = new Float32Array(count * 3);
    const tangent = new Float32Array(count * 3);
    const bitangent = new Float32Array(count * 3);
    const phase = new Float32Array(count);
    const radius = new Float32Array(count);
    const rateA = new Float32Array(count);
    const rateB = new Float32Array(count);
    const positions = new Float32Array(count * 3);

    for (let i = 0; i < count; i += 1) {
      const u = rand(i + 101);
      const v = rand(i + 102);
      const theta = u * Math.PI * 2;
      const phi = Math.acos(2 * v - 1);

      const sx = Math.sin(phi) * Math.cos(theta);
      const sy = Math.sin(phi) * Math.sin(theta);
      const sz = Math.cos(phi);

      const axis = Math.abs(sy) > 0.88 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
      const dir = new THREE.Vector3(sx, sy, sz);
      const tan = dir.clone().cross(axis).normalize();
      const bit = dir.clone().cross(tan).normalize();

      dirs[i * 3] = sx;
      dirs[i * 3 + 1] = sy;
      dirs[i * 3 + 2] = sz;
      tangent[i * 3] = tan.x;
      tangent[i * 3 + 1] = tan.y;
      tangent[i * 3 + 2] = tan.z;
      bitangent[i * 3] = bit.x;
      bitangent[i * 3 + 1] = bit.y;
      bitangent[i * 3 + 2] = bit.z;
      phase[i] = rand(i + 103) * Math.PI * 2;
      radius[i] = 0.012 + Math.pow(rand(i + 104), 2.4) * 0.14;
      rateA[i] = 0.65 + rand(i + 105) * 1.8;
      rateB[i] = 0.75 + rand(i + 106) * 2.2;
    }

    return { dirs, tangent, bitangent, phase, radius, rateA, rateB, positions };
  }, []);

  useFrame((state) => {
    if (!pointsRef.current || !coreSphereRef.current) return;

    const profile = PROFILE[agentState];
    const t = state.clock.elapsedTime;
    const p = pulseSignal(t, profile);
    const attr = pointsRef.current.geometry.getAttribute("position") as THREE.BufferAttribute;
    const collapse = 0.24 + p * 0.62;

    for (let i = 0; i < count; i += 1) {
      const idx = i * 3;
      const ph = data.phase[i];
      const ra = data.rateA[i];
      const rb = data.rateB[i];

      const base = data.radius[i] * collapse;
      const driftA = Math.sin(t * profile.coreDrift * ra + ph) * 0.009;
      const driftB = Math.cos(t * profile.coreDrift * rb - ph * 1.2) * 0.006;
      const r = base + driftA + driftB;

      const swirlA = Math.sin(t * (0.8 + ra) + ph * 1.6) * 0.016;
      const swirlB = Math.cos(t * (1.2 + rb) + ph * 0.8) * 0.013;
      attr.array[idx] = data.dirs[idx] * r + data.tangent[idx] * swirlA + data.bitangent[idx] * swirlB;
      attr.array[idx + 1] = data.dirs[idx + 1] * r + data.tangent[idx + 1] * swirlA + data.bitangent[idx + 1] * swirlB;
      attr.array[idx + 2] = data.dirs[idx + 2] * r + data.tangent[idx + 2] * swirlA + data.bitangent[idx + 2] * swirlB;
    }

    attr.needsUpdate = true;
    const mat = pointsRef.current.material as THREE.PointsMaterial;
    mat.opacity = 0.72 + p * 0.2;
    mat.color = mat.color.lerpColors(colorB, colorA, 0.28 + p * 0.46);

    coreSphereRef.current.scale.setScalar(0.7 + p * 0.12);
  });

  return (
    <group position={[0, 0, 0.08]}>
      <points ref={pointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={colorB}
          size={0.0102}
          transparent
          opacity={0.8}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>

      <mesh ref={coreSphereRef}>
        <sphereGeometry args={[0.03, 36, 36]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.98} toneMapped={false} />
      </mesh>
    </group>
  );
}

function OuterShellCluster({
  colorA,
  colorB,
  agentState,
}: {
  colorA: THREE.Color;
  colorB: THREE.Color;
  agentState: AgentState;
}) {
  const shellGroupRef = useRef<THREE.Group>(null);
  const shellPointsRef = useRef<THREE.Points>(null);
  const count = 1480;

  const data = useMemo(() => {
    const angle = new Float32Array(count);
    const ringBase = new Float32Array(count);
    const radialBias = new Float32Array(count);
    const band = new Float32Array(count);
    const phaseA = new Float32Array(count);
    const phaseB = new Float32Array(count);
    const phaseC = new Float32Array(count);
    const spinRate = new Float32Array(count);
    const positions = new Float32Array(count * 3);

    for (let i = 0; i < count; i += 1) {
      const ring = rand(i + 210) > 0.46 ? 1 : 0;
      angle[i] = rand(i + 211) * Math.PI * 2;
      ringBase[i] = ring ? 1.26 : 1.07;
      radialBias[i] = (rand(i + 212) - 0.5) * 0.12;
      band[i] = (rand(i + 213) - 0.5) * (ring ? 0.18 : 0.12);
      phaseA[i] = rand(i + 214) * Math.PI * 2;
      phaseB[i] = rand(i + 215) * Math.PI * 2;
      phaseC[i] = rand(i + 216) * Math.PI * 2;
      spinRate[i] = 0.5 + rand(i + 217) * 1.6;
    }

    return { angle, ringBase, radialBias, band, phaseA, phaseB, phaseC, spinRate, positions };
  }, []);

  useFrame((state, delta) => {
    if (!shellPointsRef.current || !shellGroupRef.current) return;
    const profile = PROFILE[agentState];
    const t = state.clock.elapsedTime;
    const p = pulseSignal(t, profile);
    const attr = shellPointsRef.current.geometry.getAttribute("position") as THREE.BufferAttribute;

    for (let i = 0; i < count; i += 1) {
      const idx = i * 3;
      const baseAngle = data.angle[i];
      const a = data.phaseA[i];
      const b = data.phaseB[i];
      const c = data.phaseC[i];
      const rate = data.spinRate[i];

      const swirl = Math.sin(t * profile.shellSpin * rate + a) * profile.shellJitter * 0.22;
      const orbital = baseAngle + t * 0.09 * rate + swirl;
      const pulse = Math.sin(t * profile.waveSpeed * (0.7 + rate * 0.4) + b) * 0.07;
      const chaos = Math.cos(t * (1.4 + rate) + c) * 0.036;
      const shellWave = Math.sin(orbital * 4.8 + t * 0.54 + a) * 0.028;
      const radial = data.ringBase[i] + data.radialBias[i] + pulse + chaos + shellWave;
      const y =
        data.band[i] +
        Math.sin(t * (1 + rate * 0.45) + b) * 0.042 +
        Math.cos(orbital * 3.2 + t * 0.34 + c) * 0.032;

      attr.array[idx] = Math.cos(orbital) * radial;
      attr.array[idx + 1] = y;
      attr.array[idx + 2] = Math.sin(orbital) * radial;
    }
    attr.needsUpdate = true;

    shellGroupRef.current.rotation.y += delta * profile.shellSpin * 0.44;
    shellGroupRef.current.rotation.x += delta * profile.shellSpin * 0.17;
    shellGroupRef.current.rotation.z += delta * profile.shellSpin * 0.08;

    const mat = shellPointsRef.current.material as THREE.PointsMaterial;
    mat.opacity = 0.36 + p * 0.16;
    mat.color = mat.color.lerpColors(colorA, colorB, 0.2 + p * 0.52);
  });

  return (
    <group ref={shellGroupRef}>
      <points ref={shellPointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={colorA}
          size={0.0088}
          transparent
          opacity={0.46}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>
    </group>
  );
}

function WaveEnvelopeCluster({
  colorA,
  colorB,
  agentState,
}: {
  colorA: THREE.Color;
  colorB: THREE.Color;
  agentState: AgentState;
}) {
  const waveGroupRef = useRef<THREE.Group>(null);
  const wavePointsRef = useRef<THREE.Points>(null);
  const count = 860;

  const data = useMemo(() => {
    const angle = new Float32Array(count);
    const baseRadius = new Float32Array(count);
    const band = new Float32Array(count);
    const phase1 = new Float32Array(count);
    const phase2 = new Float32Array(count);
    const phase3 = new Float32Array(count);
    const freq1 = new Float32Array(count);
    const freq2 = new Float32Array(count);
    const amp1 = new Float32Array(count);
    const amp2 = new Float32Array(count);
    const positions = new Float32Array(count * 3);

    for (let i = 0; i < count; i += 1) {
      angle[i] = rand(i + 301) * Math.PI * 2;
      baseRadius[i] = 1.82 + rand(i + 302) * 0.46;
      band[i] = (rand(i + 303) - 0.5) * 0.2;
      phase1[i] = rand(i + 304) * Math.PI * 2;
      phase2[i] = rand(i + 305) * Math.PI * 2;
      phase3[i] = rand(i + 306) * Math.PI * 2;
      freq1[i] = 3.2 + rand(i + 307) * 4.8;
      freq2[i] = 1.4 + rand(i + 308) * 2.6;
      amp1[i] = 0.026 + rand(i + 309) * 0.07;
      amp2[i] = 0.016 + rand(i + 310) * 0.05;
    }

    return { angle, baseRadius, band, phase1, phase2, phase3, freq1, freq2, amp1, amp2, positions };
  }, []);

  useFrame((state, delta) => {
    if (!wavePointsRef.current || !waveGroupRef.current) return;
    const profile = PROFILE[agentState];
    const t = state.clock.elapsedTime;
    const p = pulseSignal(t, profile);
    const attr = wavePointsRef.current.geometry.getAttribute("position") as THREE.BufferAttribute;

    for (let i = 0; i < count; i += 1) {
      const idx = i * 3;
      const a = data.angle[i];
      const ph1 = data.phase1[i];
      const ph2 = data.phase2[i];
      const ph3 = data.phase3[i];
      const f1 = data.freq1[i];
      const f2 = data.freq2[i];

      const breathing = 1 + Math.sin(t * profile.waveSpeed * 0.44 + ph2) * 0.06;
      const wave1 = Math.sin(a * f1 + t * profile.waveSpeed * 0.8 + ph1) * (data.amp1[i] + profile.waveAmplitude * 0.16);
      const wave2 = Math.cos(a * f2 - t * profile.waveSpeed * 0.92 + ph2) * (data.amp2[i] + profile.waveAmplitude * 0.12);
      const ringNoise = Math.sin((a + t * 0.2) * (2.8 + f2 * 0.22) + ph3) * 0.018;
      const radius = data.baseRadius[i] * breathing + wave1 + wave2 + ringNoise;
      const localAngle = a + Math.sin(t * 0.5 + ph1) * 0.09;
      const y =
        data.band[i] +
        Math.sin(t * (1 + f2 * 0.2) + ph3) * 0.075 +
        Math.cos(localAngle * (2.2 + f2 * 0.24) + t * 0.33 + ph1) * 0.048;

      attr.array[idx] = Math.cos(localAngle) * radius;
      attr.array[idx + 1] = y;
      attr.array[idx + 2] = Math.sin(localAngle) * radius;
    }

    attr.needsUpdate = true;
    waveGroupRef.current.rotation.x = 1.04 + Math.sin(t * 0.21) * 0.12;
    waveGroupRef.current.rotation.y += delta * profile.waveSpeed * 0.31;
    waveGroupRef.current.rotation.z += delta * profile.waveSpeed * 0.14;

    const mat = wavePointsRef.current.material as THREE.PointsMaterial;
    mat.opacity = 0.3 + p * 0.2;
    mat.color = mat.color.lerpColors(colorB, colorA, 0.34 + p * 0.44);
  });

  return (
    <group ref={waveGroupRef}>
      <points ref={wavePointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={colorB}
          size={0.0072}
          transparent
          opacity={0.48}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>
    </group>
  );
}

function RadialFluxCluster({
  colorA,
  colorB,
  agentState,
}: {
  colorA: THREE.Color;
  colorB: THREE.Color;
  agentState: AgentState;
}) {
  const fluxGroupRef = useRef<THREE.Group>(null);
  const fluxPointsRef = useRef<THREE.Points>(null);
  const count = 1420;

  const data = useMemo(() => {
    const angle = new Float32Array(count);
    const elev = new Float32Array(count);
    const start = new Float32Array(count);
    const reach = new Float32Array(count);
    const phaseA = new Float32Array(count);
    const phaseB = new Float32Array(count);
    const rate = new Float32Array(count);
    const positions = new Float32Array(count * 3);

    for (let i = 0; i < count; i += 1) {
      angle[i] = rand(i + 401) * Math.PI * 2;
      elev[i] = (rand(i + 402) - 0.5) * 0.58;
      start[i] = 0.04 + rand(i + 403) * 0.18;
      reach[i] = 0.86 + rand(i + 404) * 1.28;
      phaseA[i] = rand(i + 405) * Math.PI * 2;
      phaseB[i] = rand(i + 406) * Math.PI * 2;
      rate[i] = 0.6 + rand(i + 407) * 1.8;
    }

    return { angle, elev, start, reach, phaseA, phaseB, rate, positions };
  }, []);

  useFrame((state, delta) => {
    if (!fluxPointsRef.current || !fluxGroupRef.current) return;
    const profile = PROFILE[agentState];
    const t = state.clock.elapsedTime;
    const p = pulseSignal(t, profile);
    const attr = fluxPointsRef.current.geometry.getAttribute("position") as THREE.BufferAttribute;

    for (let i = 0; i < count; i += 1) {
      const idx = i * 3;
      const phA = data.phaseA[i];
      const phB = data.phaseB[i];
      const r = data.rate[i];

      const progress = 0.16 + 0.84 * Math.abs(Math.sin(t * profile.fluxSpeed * r + phA));
      const distance = data.start[i] + data.reach[i] * progress * (0.62 + p * 0.48);
      const localAngle = data.angle[i] + Math.sin(t * (1 + r * 0.33) + phB) * 0.13;

      attr.array[idx] = Math.cos(localAngle) * distance;
      attr.array[idx + 1] = data.elev[i] * distance * 0.36 + Math.sin(t * (0.7 + r * 0.24) + phA) * 0.035;
      attr.array[idx + 2] = Math.sin(localAngle) * distance;
    }

    attr.needsUpdate = true;
    fluxGroupRef.current.rotation.z += delta * profile.fluxSpeed * 0.08;
    fluxGroupRef.current.rotation.y += delta * profile.fluxSpeed * 0.14;

    const mat = fluxPointsRef.current.material as THREE.PointsMaterial;
    mat.opacity = 0.26 + p * 0.2;
    mat.color = mat.color.lerpColors(colorA, colorB, 0.54 + p * 0.32);
  });

  return (
    <group ref={fluxGroupRef}>
      <points ref={fluxPointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={colorA}
          size={0.0078}
          transparent
          opacity={0.26}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>
    </group>
  );
}

function GlowFogLayer({ colorA, colorB, agentState }: { colorA: THREE.Color; colorB: THREE.Color; agentState: AgentState }) {
  const frontRef = useRef<THREE.Mesh>(null);
  const backRef = useRef<THREE.Mesh>(null);

  useFrame((state, delta) => {
    if (!frontRef.current || !backRef.current) return;
    const profile = PROFILE[agentState];
    const t = state.clock.elapsedTime;
    const p = pulseSignal(t, profile);

    frontRef.current.rotation.z += delta * profile.shellSpin * 0.24;
    backRef.current.rotation.z -= delta * profile.shellSpin * 0.18;

    const frontMat = frontRef.current.material as THREE.MeshBasicMaterial;
    const backMat = backRef.current.material as THREE.MeshBasicMaterial;
    frontMat.opacity = THREE.MathUtils.lerp(frontMat.opacity, 0.05 + p * 0.03, delta * 2.4);
    backMat.opacity = THREE.MathUtils.lerp(backMat.opacity, 0.03 + p * 0.02, delta * 2.4);
  });

  return (
    <group>
      <mesh ref={frontRef}>
        <sphereGeometry args={[1.1, 42, 42]} />
        <meshBasicMaterial
          color={colorA}
          transparent
          opacity={0.06}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh ref={backRef}>
        <sphereGeometry args={[1.44, 36, 36]} />
        <meshBasicMaterial
          color={colorB}
          transparent
          opacity={0.045}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  );
}

function OrbScene({ colors, agentState }: Pick<OrbProps, "colors" | "agentState">) {
  const colorA = useMemo(() => new THREE.Color(colors[0]), [colors]);
  const colorB = useMemo(() => new THREE.Color(colors[1]), [colors]);

  return (
    <>
      <ambientLight intensity={0.26} color={colorB} />
      <pointLight position={[0, 0, 1.6]} intensity={2.45} color="#ffffff" />
      <pointLight position={[1.5, 0.7, -1.2]} intensity={0.86} color={colorA} />
      <pointLight position={[-1.6, -0.9, -1.2]} intensity={0.74} color={colorB} />

      <GlowFogLayer colorA={colorA} colorB={colorB} agentState={agentState} />
      <WaveEnvelopeCluster colorA={colorA} colorB={colorB} agentState={agentState} />
      <OuterShellCluster colorA={colorA} colorB={colorB} agentState={agentState} />
      <RadialFluxCluster colorA={colorA} colorB={colorB} agentState={agentState} />
      <CoreCluster colorA={colorA} colorB={colorB} agentState={agentState} />
    </>
  );
}

export function Orb({ colors, agentState }: OrbProps) {
  const [capability, setCapability] = useState<WebGLCapability>({ webgl: true, webgl2: true });

  useEffect(() => {
    setCapability(detectWebGLCapability());
  }, []);

  const fallback = <OrbFallback />;
  if (!capability.webgl) {
    return fallback;
  }

  return (
    <OrbErrorBoundary fallback={fallback}>
      <Canvas
        dpr={[1, 2]}
        camera={{ position: [0, 0, 5.25], fov: 42 }}
        gl={{ alpha: true, antialias: true, powerPreference: "high-performance" }}
        onCreated={({ gl }) => gl.setClearColor(new THREE.Color("#000000"), 0)}
      >
        <OrbScene colors={colors} agentState={agentState} />
        {capability.webgl2 ? (
          <EffectComposer autoClear={false} multisampling={0}>
            <Bloom intensity={PROFILE[agentState].bloom} luminanceThreshold={0.02} luminanceSmoothing={0.9} mipmapBlur />
          </EffectComposer>
        ) : null}
      </Canvas>
    </OrbErrorBoundary>
  );
}
