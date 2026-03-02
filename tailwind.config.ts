import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0e141b",
        panel: "#172330",
        accent: "#5dd39e",
        warn: "#ffd166",
        text: "#e6f0fa",
        muted: "#91a3b5"
      }
    }
  },
  plugins: [],
};

export default config;
