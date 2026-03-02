import type { CanonicalAgentId } from "@/lib/agents";

type MiniShapeProps = {
  id: CanonicalAgentId;
  strokeColor: string;
  size?: number;
  fill?: string;
};

export function MiniShape({ id, strokeColor, size = 16, fill = "none" }: MiniShapeProps) {
  if (id === "minerva") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size}>
        <polygon points="12,2 22,7 22,17 12,22 2,17 2,7" stroke={strokeColor} strokeWidth="1.5" fill={fill} />
      </svg>
    );
  }
  if (id === "clio") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size}>
        <polygon points="12,2 22,12 12,22 2,12" stroke={strokeColor} strokeWidth="1.5" fill={fill} />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <polygon points="12,3 22,21 2,21" stroke={strokeColor} strokeWidth="1.5" fill={fill} />
    </svg>
  );
}
