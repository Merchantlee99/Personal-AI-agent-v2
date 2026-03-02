"use client";

import React, { useRef, useMemo, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import type { AgentState } from './types';

type OrbProps = {
    colors: [string, string];
    agentState: AgentState;
};

// ==========================================
// Shader Libraries & Utilities
// ==========================================

const noiseLib = `
// 2D Random
float random (in vec2 st) {
    return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);
}

// 2D Noise based on Morgan McGuire @morgan3d
// https://www.shadertoy.com/view/4dS3Wd
float noise (in vec2 st) {
    vec2 i = floor(st);
    vec2 f = fract(st);

    // Four corners in 2D of a tile
    float a = random(i);
    float b = random(i + vec2(1.0, 0.0));
    float c = random(i + vec2(0.0, 1.0));
    float d = random(i + vec2(1.0, 1.0));

    // Smooth Interpolation
    vec2 u = f*f*(3.0-2.0*f);

    // Mix 4 coorners percentages
    return mix(a, b, u.x) +
            (c - a)* u.y * (1.0 - u.x) +
            (d - b) * u.x * u.y;
}
`;

// ==========================================
// Layer 1: Core Glow Sphere (Billboard)
// ==========================================
const coreVertex = `
varying vec2 vUv;
void main() {
    vUv = uv;
    // Billboard logic: always face camera
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const coreFragment = `
uniform vec3 colorCenter;
uniform vec3 colorEdge;
uniform float intensity;
uniform float pulse;

varying vec2 vUv;

void main() {
    vec2 uv = vUv * 2.0 - 1.0; // -1 to 1
    float dist = length(uv);
    
    // Hard cutoff at edges to prevent clipping bounds
    if(dist > 0.99) {
       discard;
    }
    
    // Core is solid white, fading into color Edge
    float core = 1.0 - smoothstep(0.0, 0.4 + pulse*0.05, dist);
    float outerGlow = 1.0 - smoothstep(0.3, 0.9, dist); // Fades beautifully to 0 before the edge of the quad
    
    vec3 mixedColor = mix(colorEdge, colorCenter, core);
    
    // Calculate final alpha ensuring it perfectly hits 0 at the edge
    float alpha = outerGlow * intensity;
    
    gl_FragColor = vec4(mixedColor * alpha, alpha);
}
`;

// ==========================================
// Layer 2: Radiating Energy Rays (Polar Shader)
// ==========================================
const raysFragment = `
uniform vec3 color;
uniform float time;
uniform float speed;
uniform float rayDensity;
uniform float intensity;

varying vec2 vUv;

${noiseLib}

void main() {
    vec2 uv = vUv * 2.0 - 1.0;
    float dist = length(uv);
    
    if(dist > 0.99) discard; // Strict circular bound
    
    // Convert to polar coordinates: angle and radius
    float angle = atan(uv.y, uv.x);
    
    // We sample noise based on the angle to create "beams"
    // We add time to make the beams rotate slowly
    float n1 = noise(vec2(angle * rayDensity, time * speed));
    float n2 = noise(vec2(angle * rayDensity * 2.0, -time * speed * 0.5));
    
    float mergedNoise = n1 * n2;
    
    // Sharpen the noise to make distinct rays
    mergedNoise = smoothstep(0.4, 0.8, mergedNoise);
    
    // Mask out the center (so rays shoot OUT from the core)
    float innerMask = smoothstep(0.1, 0.3, dist);
    // Mask out the very edge so they fade completely before the quad bounds
    float outerMask = 1.0 - smoothstep(0.6, 1.0, dist);
    
    float alpha = mergedNoise * innerMask * outerMask * intensity;
    
    gl_FragColor = vec4(color * alpha, alpha);
}
`;

// ==========================================
// Layer 3: Expanding Magic Waves
// ==========================================
const wavesFragment = `
uniform vec3 color;
uniform float time;
uniform float speed;
uniform float frequency;
uniform float intensity;

varying vec2 vUv;

${noiseLib}

void main() {
    vec2 uv = vUv * 2.0 - 1.0;
    float dist = length(uv);
    
    if(dist > 0.99) discard;
    
    float angle = atan(uv.y, uv.x);
    
    // Create concentric expanding rings
    // sin(distance * freq - t)
    float rings = sin(dist * frequency - time * speed * 10.0);
    
    // Only keep the peaks of the sine wave
    rings = smoothstep(0.8, 1.0, rings);
    
    // Add noise to break up the perfect rings into organic waves
    float n = noise(vec2(angle * 5.0, dist * 5.0 - time * speed * 2.0));
    
    float mask = rings * n;
    
    // Fade at center and edges
    float centerMask = smoothstep(0.1, 0.4, dist);
    float edgeMask = 1.0 - smoothstep(0.6, 1.0, dist);
    
    float alpha = mask * centerMask * edgeMask * intensity;
    
    // Boost brightness of the waves
    gl_FragColor = vec4(color * 1.5 * alpha, alpha);
}
`;

// ==========================================
// Component Implementations
// ==========================================

const AnimatedPlane = ({
    vertex,
    fragment,
    uniforms,
    size = 4,
    zDepth = 0,
    updateUniforms
}: any) => {
    const matRef = useRef<THREE.ShaderMaterial>(null);

    useFrame((state, delta) => {
        if (!matRef.current) return;
        updateUniforms(matRef.current.uniforms, state, delta);
    });

    return (
        <mesh position={[0, 0, zDepth]}>
            <planeGeometry args={[size, size]} />
            <shaderMaterial
                ref={matRef}
                vertexShader={vertex}
                fragmentShader={fragment}
                uniforms={uniforms}
                transparent={true}
                depthWrite={false}
                blending={THREE.AdditiveBlending}
            />
        </mesh>
    );
};


const CoreSphere = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const uniforms = useMemo(() => ({
        colorCenter: { value: new THREE.Color("#ffffff") }, // Core is hot white
        colorEdge: { value: color },
        intensity: { value: 1.0 },
        pulse: { value: 0.0 }
    }), [color]);

    const update = (u: any, state: any, delta: number) => {
        let targetIntensity = 1.0;
        let pulseVal = 0.0;

        if (agentState === 'listening') targetIntensity = 1.5;
        if (agentState === 'thinking') targetIntensity = 2.0;

        if (agentState === 'speaking') {
            pulseVal = Math.sin(state.clock.elapsedTime * 15);
        } else if (agentState === 'idle') {
            pulseVal = Math.sin(state.clock.elapsedTime * 2) * 0.5;
        }

        u.intensity.value = THREE.MathUtils.lerp(u.intensity.value, targetIntensity, delta * 5);
        u.pulse.value = THREE.MathUtils.lerp(u.pulse.value, pulseVal, delta * 10);
    };

    return <AnimatedPlane vertex={coreVertex} fragment={coreFragment} uniforms={uniforms} size={3} updateUniforms={update} />;
};


const EnergyRays = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const uniforms = useMemo(() => ({
        time: { value: 0 },
        color: { value: color },
        speed: { value: 0.5 },
        rayDensity: { value: 3.0 },
        intensity: { value: 1.5 }
    }), [color]);

    const update = (u: any, state: any, delta: number) => {
        u.time.value = state.clock.elapsedTime;

        let tSpeed = 0.5;
        let tInt = 1.5;
        let tDen = 3.0;

        if (agentState === 'listening') {
            tSpeed = 1.0; tInt = 2.5;
        } else if (agentState === 'thinking') {
            tSpeed = 3.0; tInt = 3.0; tDen = 6.0; // Chaotic fast rays
        } else if (agentState === 'speaking') {
            tSpeed = 1.0; tInt = 2.0;
            tDen = 3.0 + Math.sin(state.clock.elapsedTime * 10); // Rhythmic rays
        }

        u.speed.value = THREE.MathUtils.lerp(u.speed.value, tSpeed, delta * 4);
        u.intensity.value = THREE.MathUtils.lerp(u.intensity.value, tInt, delta * 4);
        u.rayDensity.value = THREE.MathUtils.lerp(u.rayDensity.value, tDen, delta * 2);
    };

    return <AnimatedPlane vertex={coreVertex} fragment={raysFragment} uniforms={uniforms} size={6} zDepth={-0.1} updateUniforms={update} />;
};


const MagicWaves = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    // slightly offset color for waves
    const waveColor = useMemo(() => {
        const c = color.clone();
        c.lerp(new THREE.Color("#ffffff"), 0.3);
        return c;
    }, [color]);

    const uniforms = useMemo(() => ({
        time: { value: 0 },
        color: { value: waveColor },
        speed: { value: 0.5 },
        frequency: { value: 15.0 },
        intensity: { value: 1.2 }
    }), [waveColor]);

    const update = (u: any, state: any, delta: number) => {
        u.time.value = state.clock.elapsedTime;

        let tSpeed = 0.5;
        let tFreq = 15.0;
        let tInt = 1.2;

        if (agentState === 'listening') {
            tSpeed = 1.0; tFreq = 20.0; tInt = 1.8;
        } else if (agentState === 'thinking') {
            tSpeed = 2.0; tFreq = 30.0; tInt = 2.5;
        } else if (agentState === 'speaking') {
            tSpeed = 1.5;
            // Rhythmically adjust frequency to mimic sound waves
            tFreq = 15.0 + Math.sin(state.clock.elapsedTime * 12) * 5.0;
            tInt = 2.0;
        }

        u.speed.value = THREE.MathUtils.lerp(u.speed.value, tSpeed, delta * 4);
        u.frequency.value = THREE.MathUtils.lerp(u.frequency.value, tFreq, delta * 4);
        u.intensity.value = THREE.MathUtils.lerp(u.intensity.value, tInt, delta * 4);
    };

    return <AnimatedPlane vertex={coreVertex} fragment={wavesFragment} uniforms={uniforms} size={5} zDepth={0.1} updateUniforms={update} />;
};


const RadialParticles = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const pointsRef = useRef<THREE.Points>(null);
    const count = 500;

    // We update points manually to simulate radial emission
    const [positions] = useState(() => new Float32Array(count * 3));
    const [lifetimes] = useState(() => new Float32Array(count)); // 0 to 1
    const [angles] = useState(() => new Float32Array(count));
    const [speeds] = useState(() => new Float32Array(count));

    useMemo(() => {
        for (let i = 0; i < count; i++) {
            lifetimes[i] = Math.random(); // staggered starts
            angles[i] = Math.random() * Math.PI * 2.0;
            speeds[i] = 1.0 + Math.random() * 2.0;
        }
    }, [count, lifetimes, angles, speeds]);

    useFrame((_, delta) => {
        if (!pointsRef.current) return;
        const attr = pointsRef.current.geometry.attributes.position as THREE.BufferAttribute;

        let globalSpeed = 0.5;
        if (agentState === 'thinking') globalSpeed = 2.0;
        if (agentState === 'listening') globalSpeed = 1.0;
        if (agentState === 'speaking') globalSpeed = 1.2;

        for (let i = 0; i < count; i++) {
            lifetimes[i] += delta * speeds[i] * globalSpeed;
            if (lifetimes[i] >= 1.0) {
                lifetimes[i] = 0.0; // Reset
                angles[i] = Math.random() * Math.PI * 2.0; // new direction
            }

            // Calculate radius based on lifetime (starts at center, moves out to radius 3)
            const r = lifetimes[i] * 3.0;

            attr.array[i * 3] = Math.cos(angles[i]) * r;
            attr.array[i * 3 + 1] = Math.sin(angles[i]) * r;
            attr.array[i * 3 + 2] = 0.2; // slightly in front
        }
        attr.needsUpdate = true;
    });

    return (
        <points ref={pointsRef}>
            <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[positions, 3]} />
            </bufferGeometry>
            <pointsMaterial size={0.05} color={color} transparent={true} opacity={0.6} blending={THREE.AdditiveBlending} depthWrite={false} sizeAttenuation={true} />
        </points>
    );
};

// ==========================================
// Main Components
// ==========================================

export function BillboardMagicOrb({ agentState, colors }: OrbProps) {
    const mainColorStr = colors[0];
    const mainColor = useMemo(() => new THREE.Color(mainColorStr), [mainColorStr]);

    return (
        <group>
            {/* Layers rendered back to front */}
            <EnergyRays color={mainColor} agentState={agentState} />
            <MagicWaves color={mainColor} agentState={agentState} />
            <CoreSphere color={mainColor} agentState={agentState} />
            <RadialParticles color={mainColor} agentState={agentState} />
        </group>
    );
}

// Wrapper Canvas
export function Orb(props: OrbProps) {
    return (
        <Canvas camera={{ position: [0, 0, 5], fov: 50 }}>
            {/* Purely shader driven, no lights necessary */}
            <BillboardMagicOrb {...props} />

            <EffectComposer>
                <Bloom
                    intensity={2.5}
                    luminanceThreshold={0.05}
                    luminanceSmoothing={0.9}
                    mipmapBlur
                />
            </EffectComposer>
        </Canvas>
    );
}
