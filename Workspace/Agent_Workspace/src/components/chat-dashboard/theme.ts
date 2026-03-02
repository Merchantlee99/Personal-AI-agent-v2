export type DashboardTheme = "dark" | "light";

export type ThemeColors = {
  bg: string;
  bgSecondary: string;
  border: string;
  borderHover: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  textFaint: string;
  surface: string;
  surfaceHover: string;
  sendButton: string;
  sendIcon: string;
  sendDisabled: string;
};

export const DARK_THEME: ThemeColors = {
  bg: "#09090B",
  bgSecondary: "#18181B",
  border: "#27272A",
  borderHover: "#3F3F46",
  textPrimary: "#FAFAFA",
  textSecondary: "#A1A1AA",
  textMuted: "#71717A",
  textFaint: "#52525B",
  surface: "#18181B",
  surfaceHover: "#27272A",
  sendButton: "#FAFAFA",
  sendIcon: "#09090B",
  sendDisabled: "#27272A",
};

export const LIGHT_THEME: ThemeColors = {
  bg: "#FFFFFF",
  bgSecondary: "#F4F4F5",
  border: "#E4E4E7",
  borderHover: "#D4D4D8",
  textPrimary: "#09090B",
  textSecondary: "#52525B",
  textMuted: "#71717A",
  textFaint: "#A1A1AA",
  surface: "#F4F4F5",
  surfaceHover: "#E4E4E7",
  sendButton: "#09090B",
  sendIcon: "#FAFAFA",
  sendDisabled: "#E4E4E7",
};
