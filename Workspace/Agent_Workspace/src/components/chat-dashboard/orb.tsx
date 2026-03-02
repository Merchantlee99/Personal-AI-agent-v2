"use client";

import React, { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import type { AgentState } from './types';

type OrbProps = {
    colors: [string, string];
    agentState: AgentState;
};

// -----------------------------------------------------
// Utility: 3D Simplex Noise from Ashima Arts
// -----------------------------------------------------
const snoise3D = `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+10.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) { 
  const vec2  C = vec2(1.0/6.0, 1.0/3.0) ;
  const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);

  vec3 i  = floor(v + dot(v, C.yyy) );
  vec3 x0 =   v - i + dot(i, C.xxx) ;

  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min( g.xyz, l.zxy );
  vec3 i2 = max( g.xyz, l.zxy );

  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;

  i = mod289(i); 
  vec4 p = permute( permute( permute( 
             i.z + vec4(0.0, i1.z, i2.z, 1.0 ))
           + i.y + vec4(0.0, i1.y, i2.y, 1.0 )) 
           + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));

  float n_ = 0.142857142857;
  vec3  ns = n_ * D.wyz - D.xzx;

  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_ );

  vec4 x = x_ *ns.x + ns.yyyy;
  vec4 y = y_ *ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);

  vec4 b0 = vec4( x.xy, y.xy );
  vec4 b1 = vec4( x.zw, y.zw );

  vec4 s0 = floor(b0)*2.0 + 1.0;
  vec4 s1 = floor(b1)*2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));

  vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
  vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;

  vec3 p0 = vec3(a0.xy,h.x);
  vec3 p1 = vec3(a0.zw,h.y);
  vec3 p2 = vec3(a1.xy,h.z);
  vec3 p3 = vec3(a1.zw,h.w);

  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
  p0 *= norm.x;
  p1 *= norm.y;
  p2 *= norm.z;
  p3 *= norm.w;

  vec4 m = max(0.5 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 105.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), 
                                dot(p2,x2), dot(p3,x3) ) );
}
`;

// -----------------------------------------------------
// Shaders
// -----------------------------------------------------

// --- LAYER 1: Core Aura ---
const auraVertexShader = `
varying vec2 vUv;
void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const auraFragmentShader = `
uniform vec3 color;
uniform float intensity;
varying vec2 vUv;

void main() {
    vec2 pos = vUv - 0.5;
    float dist = length(pos);
    
    // Smooth falloff
    float alpha = smoothstep(0.5, 0.0, dist);
    // Hot center
    float core = smoothstep(0.15, 0.0, dist) * 1.5;
    
    vec3 finalColor = color * intensity * (alpha + core);
    gl_FragColor = vec4(finalColor, alpha * 0.5);
}
`;

// --- LAYER 2: Fibonacci Particle Wave Sphere ---
const waveVertexShader = `
uniform float time;
uniform float particleSize;
uniform float waveSpeed;
uniform float frequency;
uniform float amplitude;

varying float vIntensity;

${snoise3D}

void main() {
    vec3 pos = position;
    
    // We do NOT displace the particle radically to preserve the spherical ghost shape.
    // Instead, we use noise to calculate how "bright" and "large" this particle should be.
    // The noise travels through the coordinates over time, creating moving waves of particles.
    
    vec3 noisePos = pos * frequency + vec3(time * waveSpeed, time * waveSpeed * 0.5, time * waveSpeed * 0.2);
    
    // Combine two noise layers for more wispy, chaotic threads
    float n1 = snoise(noisePos);
    float n2 = snoise(noisePos * 2.5 - vec3(0.0, time * waveSpeed, 0.0));
    
    float noiseVal = (n1 + n2 * 0.5);
    
    // Base intensity: areas where noise is high become visible waves
    // abs() creates sharp, thread-like ribbons of energy
    float waveIntensity = smoothstep(0.1, 0.8, abs(noiseVal) * amplitude);
    
    vIntensity = waveIntensity;
    
    // Slight jitter to particle position based on noise to make it feel alive
    // But kept very small to avoid sticky stretching
    pos += normal * (noiseVal * 0.05);

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    
    // Size varies radically. "Active" particles in the wave are huge, inactive are invisible/tiny
    float finalSize = particleSize * (0.1 + waveIntensity * 2.0);
    
    gl_PointSize = finalSize * (1.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
}
`;

const waveFragmentShader = `
uniform vec3 color1;
uniform vec3 color2;
varying float vIntensity;

void main() {
    // Soft circle for the point
    vec2 coord = gl_PointCoord - vec2(0.5);
    float dist = length(coord);
    float alpha = smoothstep(0.5, 0.1, dist);
    
    if (alpha < 0.01) discard;
    
    // Mix colors based on how intense the wave is
    vec3 outputColor = mix(color2, color1, vIntensity);
    
    // Final alpha is a combination of the point alpha and the wave intensity
    // We square intensity to make the dark areas completely disappear, revealing only ribbons
    float finalAlpha = alpha * pow(vIntensity, 1.5);
    
    gl_FragColor = vec4(outputColor * 2.0, finalAlpha);
}
`;


// --- LAYER 3: Wandering Sparks ---
const sparkVertexShader = `
uniform float time;
uniform float speed;
attribute float randomScale;
attribute float randomOffset;

varying float vAlpha;
${snoise3D}

void main() {
    vec3 pos = position;
    
    // Chaotic wandering
    float t = time * speed + randomOffset;
    pos.x += snoise(vec3(pos.y, pos.z, t)) * 0.3;
    pos.y += snoise(vec3(pos.x, pos.z, t)) * 0.3;
    pos.z += snoise(vec3(pos.x, pos.y, t)) * 0.3;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = (15.0 * randomScale) * (1.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
    
    // Fade out based on distance from center to hide boundaries
    float distFromCenter = length(pos);
    vAlpha = smoothstep(3.5, 1.0, distFromCenter) * 0.6;
}
`;

const sparkFragmentShader = `
uniform vec3 color;
varying float vAlpha;

void main() {
    vec2 coord = gl_PointCoord - vec2(0.5);
    float dist = length(coord);
    float alpha = smoothstep(0.5, 0.0, dist);
    
    if (alpha < 0.05) discard;
    gl_FragColor = vec4(color * 1.5, alpha * vAlpha);
}
`;

// -----------------------------------------------------
// Geometries & Generators
// -----------------------------------------------------

function generateFibonacciSphere(samples: number, radius: number) {
    const positions = new Float32Array(samples * 3);
    const phi = Math.PI * (3.0 - Math.sqrt(5.0)); // golden angle

    for (let i = 0; i < samples; i++) {
        const y = 1 - (i / (samples - 1)) * 2; // y goes from 1 to -1
        const radiusAtY = Math.sqrt(1 - y * y); // radius at y

        const theta = phi * i; // golden angle increment

        const x = Math.cos(theta) * radiusAtY;
        const z = Math.sin(theta) * radiusAtY;

        positions[i * 3] = x * radius;
        positions[i * 3 + 1] = y * radius;
        positions[i * 3 + 2] = z * radius;
    }
    return positions;
}

function generateVolumetricSparks(count: number, maxRadius: number) {
    const p = new Float32Array(count * 3);
    const s = new Float32Array(count);
    const offset = new Float32Array(count);

    for (let i = 0; i < count; i++) {
        const u = Math.random();
        const v = Math.random();
        const theta = u * 2.0 * Math.PI;
        const phi = Math.acos(2.0 * v - 1.0);

        // Cube root for even volumetric distribution
        const r = maxRadius * Math.cbrt(Math.random());

        p[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        p[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        p[i * 3 + 2] = r * Math.cos(phi);

        s[i] = Math.random() * 0.5 + 0.5; // size
        offset[i] = Math.random() * 100.0; // random time offset
    }
    return { positions: p, randomScales: s, randomOffsets: offset };
}


// -----------------------------------------------------
// React Components
// -----------------------------------------------------

const AuraBackground = ({ color }: { color: THREE.Color }) => {
    const materialRef = useRef<THREE.ShaderMaterial>(null);
    const uniforms = useMemo(() => ({
        color: { value: color },
        intensity: { value: 0.6 } // Soft ambient glow
    }), [color]);

    return (
        <mesh position={[0, 0, -2]}>
            <planeGeometry args={[12, 12]} />
            <shaderMaterial
                ref={materialRef}
                vertexShader={auraVertexShader}
                fragmentShader={auraFragmentShader}
                uniforms={uniforms}
                transparent={true}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
            />
        </mesh>
    );
};

const ParticleWaveSphere = ({ color, secondaryColor, agentState }: { color: THREE.Color, secondaryColor: THREE.Color, agentState: AgentState }) => {
    const pointsRef = useRef<THREE.Points>(null);
    const materialRef = useRef<THREE.ShaderMaterial>(null);

    // High Density! 40,000 points perfectly distributed
    const positions = useMemo(() => generateFibonacciSphere(40000, 1.6), []);

    // Normal vectors (pointing outwards from sphere center) needed for slight noise offset
    const normals = useMemo(() => {
        const base = new Float32Array(positions.length);
        for (let i = 0; i < positions.length; i += 3) {
            const v = new THREE.Vector3(positions[i], positions[i + 1], positions[i + 2]).normalize();
            base[i] = v.x; base[i + 1] = v.y; base[i + 2] = v.z;
        }
        return base;
    }, [positions]);

    const geometry = useMemo(() => {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
        return geo;
    }, [positions, normals]);

    const uniforms = useMemo(() => ({
        time: { value: 0 },
        color1: { value: color },
        color2: { value: secondaryColor },
        particleSize: { value: 15.0 },
        waveSpeed: { value: 0.3 },
        frequency: { value: 1.5 },
        amplitude: { value: 1.2 }
    }), [color, secondaryColor]);

    const targetParams = useRef({
        waveSpeed: 0.3,
        frequency: 1.5,
        amplitude: 1.2,
        scale: 1.0,
        yRotSpeed: 0.05
    });

    useFrame((state, delta) => {
        if (!materialRef.current || !pointsRef.current) return;

        const time = state.clock.elapsedTime;
        materialRef.current.uniforms.time.value = time;

        switch (agentState) {
            case 'thinking':
                // Highly chaotic, fast flickering storms
                targetParams.current = { waveSpeed: 1.5, frequency: 2.5, amplitude: 2.0, scale: 1.15, yRotSpeed: 0.8 };
                break;
            case 'speaking':
                // Rhythmic expanding pulses
                const pulse = Math.sin(time * 12) * 0.15;
                targetParams.current = { waveSpeed: 0.8, frequency: 1.2, amplitude: 1.8 + pulse, scale: 1.05 + pulse, yRotSpeed: 0.2 };
                break;
            case 'listening':
                // Alert, bright fluid waves
                targetParams.current = { waveSpeed: 0.6, frequency: 1.8, amplitude: 1.5, scale: 1.02, yRotSpeed: 0.15 };
                break;
            case 'idle':
            default:
                // Slow, deep ocean-like drift
                targetParams.current = { waveSpeed: 0.2, frequency: 1.2, amplitude: 1.0, scale: 1.0, yRotSpeed: 0.05 };
                break;
        }

        const u = materialRef.current.uniforms;
        const lerpSpeed = delta * 3.0;

        u.waveSpeed.value = THREE.MathUtils.lerp(u.waveSpeed.value, targetParams.current.waveSpeed, lerpSpeed);
        u.frequency.value = THREE.MathUtils.lerp(u.frequency.value, targetParams.current.frequency, lerpSpeed);
        u.amplitude.value = THREE.MathUtils.lerp(u.amplitude.value, targetParams.current.amplitude, lerpSpeed);

        const curScale = pointsRef.current.scale.x;
        pointsRef.current.scale.setScalar(THREE.MathUtils.lerp(curScale, targetParams.current.scale, lerpSpeed * 2));

        // Physically rotate the entire sphere of particles slowly
        pointsRef.current.rotation.y += delta * targetParams.current.yRotSpeed;
        pointsRef.current.rotation.x += delta * 0.02; // very slow drift
    });

    return (
        <points ref={pointsRef} geometry={geometry}>
            <shaderMaterial
                ref={materialRef}
                vertexShader={waveVertexShader}
                fragmentShader={waveFragmentShader}
                uniforms={uniforms}
                transparent={true}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
            />
        </points>
    );
};

const AmbientSparks = ({ color }: { color: THREE.Color }) => {
    const pointsRef = useRef<THREE.Points>(null);
    const materialRef = useRef<THREE.ShaderMaterial>(null);

    // 3000 loose volumetric sparks traversing space
    const { positions, randomScales, randomOffsets } = useMemo(() => generateVolumetricSparks(3000, 3.0), []);

    const geometry = useMemo(() => {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geo.setAttribute('randomScale', new THREE.BufferAttribute(randomScales, 1));
        geo.setAttribute('randomOffset', new THREE.BufferAttribute(randomOffsets, 1));
        return geo;
    }, [positions, randomScales, randomOffsets]);

    const uniforms = useMemo(() => ({
        time: { value: 0 },
        color: { value: color },
        speed: { value: 0.5 }
    }), [color]);

    useFrame((state, delta) => {
        if (!pointsRef.current || !materialRef.current) return;
        materialRef.current.uniforms.time.value = state.clock.elapsedTime;
        pointsRef.current.rotation.y -= delta * 0.1;
    });

    return (
        <points ref={pointsRef} geometry={geometry}>
            <shaderMaterial
                ref={materialRef}
                vertexShader={sparkVertexShader}
                fragmentShader={sparkFragmentShader}
                uniforms={uniforms}
                transparent={true}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
            />
        </points>
    );
};


export function ComplexEnergyOrb({ agentState, colors }: OrbProps) {
    const mainColorStr = colors[0];
    const secColorStr = colors[1] || colors[0];

    const glow = useMemo(() => new THREE.Color(mainColorStr), [mainColorStr]);

    // Derive a darker, slightly rotated hue for the dark segments of the wave to give volume
    const darkAccent = useMemo(() => {
        const c = new THREE.Color(secColorStr);
        const hsl = { h: 0, s: 0, l: 0 };
        c.getHSL(hsl);
        c.setHSL(hsl.h + 0.05, hsl.s, hsl.l * 0.2); // Shift hue slightly, drop lightness heavily
        return c;
    }, [secColorStr]);

    return (
        <group>
            <AuraBackground color={glow} />
            {/* The core structure is solely dense particles forming noise waves */}
            <ParticleWaveSphere color={glow} secondaryColor={darkAccent} agentState={agentState} />
            <AmbientSparks color={glow} />
        </group>
    );
}

// Wrapper Canvas
export function Orb(props: OrbProps) {
    return (
        <Canvas camera={{ position: [0, 0, 4.8], fov: 50 }}>
            <ComplexEnergyOrb {...props} />

            <EffectComposer>
                <Bloom
                    intensity={1.8}
                    luminanceThreshold={0.0}
                    luminanceSmoothing={0.9}
                    mipmapBlur
                />
            </EffectComposer>
        </Canvas>
    );
}
