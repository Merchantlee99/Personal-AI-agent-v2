"use client";

import React, { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Points, PointMaterial } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import type { AgentState } from './types';

type OrbProps = {
    colors: [string, string]; // [glow, secondary]
    agentState: AgentState;
};

// 랜덤 파티클 생성 함수
function generateParticles(count: number, radius: number, isVolumetric: boolean = false) {
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
        const u = Math.random();
        const v = Math.random();
        const theta = u * 2.0 * Math.PI;
        const phi = Math.acos(2.0 * v - 1.0);

        let r = radius;
        if (isVolumetric) {
            r = radius * Math.cbrt(Math.random());
        }

        positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        positions[i * 3 + 2] = r * Math.cos(phi);
    }
    return positions;
}

const CoreEnergy = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const meshRef = useRef<THREE.Points>(null);
    const geometry = useMemo(() => new THREE.SphereGeometry(0.5, 32, 32), []);

    useFrame((state, delta) => {
        if (!meshRef.current) return;
        meshRef.current.rotation.y += delta * 0.5;
        meshRef.current.rotation.x += delta * 0.3;

        let targetScale = 1.0;
        if (agentState === 'thinking') targetScale = 1.2;
        else if (agentState === 'speaking') {
            targetScale = 1.1 + Math.sin(state.clock.elapsedTime * 15) * 0.05;
        }

        meshRef.current.scale.lerp(new THREE.Vector3(targetScale, targetScale, targetScale), delta * 5);
    });

    return (
        <points ref={meshRef} geometry={geometry}>
            <pointsMaterial
                color={color}
                size={0.02}
                transparent={true}
                opacity={1.0}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
                sizeAttenuation={true}
            />
        </points>
    );
};

const IcosahedronEnergyMesh = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const meshRef = useRef<THREE.Mesh>(null);
    const geometry = useMemo(() => new THREE.IcosahedronGeometry(1.5, 30), []);

    // Save original position data
    const originalPositions = useMemo(() => {
        const positions = geometry.attributes.position.array;
        return new Float32Array(positions);
    }, [geometry]);

    // target parameter based on state
    const targetParams = useMemo(() => {
        switch (agentState) {
            case 'thinking':
                // 격렬한 진동, 빠른 회전, 불규칙 팽창
                return { speed: 3.0, distortion: 0.15, frequency: 5.0, scale: 1.2 };
            case 'listening':
                // 회전속도 약간 증가
                return { speed: 1.5, distortion: 0.05, frequency: 2.0, scale: 1.05 };
            case 'speaking':
                // 리듬감있는 바운스 (진동)
                return { speed: 2.0, distortion: 0.1, frequency: 8.0, scale: 1.15 };
            case 'idle':
            default:
                // 아주 천천히 회전, 미세한 호흡
                return { speed: 0.5, distortion: 0.02, frequency: 1.5, scale: 1.0 };
        }
    }, [agentState]);

    const currentParams = useRef({ speed: 0.5, distortion: 0.02, frequency: 1.5, scale: 1.0, bounceTime: 0 });

    useFrame((state, delta) => {
        if (!meshRef.current) return;

        // 보간 (Smooth transition between states)
        currentParams.current.speed = THREE.MathUtils.lerp(currentParams.current.speed, targetParams.speed, delta * 3);
        currentParams.current.distortion = THREE.MathUtils.lerp(currentParams.current.distortion, targetParams.distortion, delta * 3);
        currentParams.current.frequency = THREE.MathUtils.lerp(currentParams.current.frequency, targetParams.frequency, delta * 3);
        currentParams.current.scale = THREE.MathUtils.lerp(currentParams.current.scale, targetParams.scale, delta * 3);

        const time = state.clock.elapsedTime;

        // Mesh Rotation
        meshRef.current.rotation.y += delta * currentParams.current.speed * 0.5;
        meshRef.current.rotation.x += delta * currentParams.current.speed * 0.2;
        meshRef.current.rotation.z += delta * currentParams.current.speed * 0.1;

        // Scale heartbeat / bounce
        let currentScale = currentParams.current.scale;
        if (agentState === 'speaking') {
            // 리듬감 있는 바운스
            currentParams.current.bounceTime += delta;
            const bounce = Math.sin(time * 12) * Math.sin(time * 3) * 0.08;
            currentScale += bounce;
        } else if (agentState === 'idle') {
            // 호흡 애니메이션
            currentScale += Math.sin(time * 1.5) * 0.03;
        } else if (agentState === 'thinking') {
            // 불규칙 팽창
            currentScale += Math.sin(time * 10) * Math.cos(time * 5) * 0.05;
        }
        meshRef.current.scale.setScalar(currentScale);

        // Vertex Distortion
        const positions = meshRef.current.geometry.attributes.position.array as Float32Array;
        const dist = currentParams.current.distortion;
        const freq = currentParams.current.frequency;

        for (let i = 0; i < positions.length; i += 3) {
            const ox = originalPositions[i];
            const oy = originalPositions[i + 1];
            const oz = originalPositions[i + 2];

            // 3D vector length 
            const length = Math.sqrt(ox * ox + oy * oy + oz * oz);
            if (length === 0) continue;
            const nx = ox / length;
            const ny = oy / length;
            const nz = oz / length;

            // Simple pseudo-random noise
            let noise = Math.sin(nx * freq + time * 2.0)
                * Math.cos(ny * freq + time * 2.5)
                * Math.sin(nz * freq + time * 3.0);

            const modifiedLength = length + noise * dist;

            positions[i] = nx * modifiedLength;
            positions[i + 1] = ny * modifiedLength;
            positions[i + 2] = nz * modifiedLength;
        }

        meshRef.current.geometry.attributes.position.needsUpdate = true;
    });

    return (
        <mesh ref={meshRef} geometry={geometry}>
            <meshStandardMaterial
                color={color}
                emissive={color}
                emissiveIntensity={3.0}
                wireframe={true}
                transparent={true}
                opacity={0.4}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
            />
        </mesh>
    );
};

const OrbitingParticles = ({ color, agentState }: { color: THREE.Color, agentState: AgentState }) => {
    const pointsRef = useRef<THREE.Points>(null);
    const count = 3000;

    // 파티클 생성
    const positions = useMemo(() => generateParticles(count, 2.5, true), [count]);

    const geometry = useMemo(() => {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        return geo;
    }, [positions]);

    const materialRef = useRef<THREE.PointsMaterial>(null);

    useFrame((state, delta) => {
        if (!pointsRef.current) return;

        let speed = 0.5;
        if (agentState === 'thinking') speed = 2.0;
        else if (agentState === 'listening') speed = 1.2;
        else if (agentState === 'speaking') speed = 1.0;

        pointsRef.current.rotation.y += delta * speed;
        pointsRef.current.rotation.x += delta * speed * 0.2;

        if (materialRef.current) {
            let targetOpacity = agentState === 'listening' ? 1.0 : 0.6;
            let targetSize = agentState === 'listening' ? 0.04 : 0.02;

            materialRef.current.opacity = THREE.MathUtils.lerp(materialRef.current.opacity, targetOpacity, delta * 3);
            materialRef.current.size = THREE.MathUtils.lerp(materialRef.current.size, targetSize, delta * 3);
        }
    });

    return (
        <points ref={pointsRef} geometry={geometry}>
            <pointsMaterial
                ref={materialRef}
                color={color}
                size={0.02}
                transparent={true}
                opacity={0.6}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
                sizeAttenuation={true}
            />
        </points>
    );
};

function ComplexEnergyOrb({ agentState, colors }: OrbProps) {
    const mainColor = useMemo(() => new THREE.Color(colors[0]), [colors[0]]);
    const secondaryColor = useMemo(() => new THREE.Color(colors[1] || colors[0]), [colors[1], colors[0]]);

    return (
        <group>
            {/* Layer 1: Core Energy */}
            <CoreEnergy color={mainColor} agentState={agentState} />

            {/* Layer 2: Undulating Energy Mesh */}
            <IcosahedronEnergyMesh color={mainColor} agentState={agentState} />

            {/* Layer 3: Orbiting Particles */}
            <OrbitingParticles color={secondaryColor} agentState={agentState} />
        </group>
    );
}

// 래퍼 캔버스 컴포넌트
export function Orb(props: OrbProps) {
    return (
        <Canvas camera={{ position: [0, 0, 4.5], fov: 50 }}>
            {/* 기본 조명 */}
            <ambientLight intensity={1.5} />
            <pointLight position={[0, 0, 0]} intensity={2.0} color={props.colors[0]} />

            <ComplexEnergyOrb {...props} />

            {/* 필수 기술 스펙: Post-processing Bloom (발광 후처리) */}
            <EffectComposer>
                <Bloom
                    intensity={2.0}
                    luminanceThreshold={0.1}
                    luminanceSmoothing={0.9}
                    mipmapBlur
                />
            </EffectComposer>
        </Canvas>
    );
}
