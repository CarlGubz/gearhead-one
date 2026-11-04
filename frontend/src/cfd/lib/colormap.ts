export type ColormapName = "viridis" | "turbo" | "coolwarm" | "inferno" | "gray";

type Stop = [number, number, number];

const STOPS: Record<ColormapName, Stop[]> = {
  viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]],
  inferno: [[0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85], [227, 89, 51], [249, 140, 10], [249, 201, 50], [252, 255, 164]],
  turbo: [[48, 18, 59], [70, 107, 227], [40, 187, 236], [49, 241, 170], [162, 252, 60], [237, 207, 58], [251, 128, 34], [208, 42, 5], [122, 4, 3]],
  coolwarm: [[59, 76, 192], [98, 130, 234], [141, 176, 254], [184, 208, 249], [221, 221, 221], [245, 196, 173], [244, 154, 123], [222, 96, 77], [180, 4, 38]],
  gray: [[20, 20, 20], [235, 235, 235]],
};

export const COLORMAPS = Object.keys(STOPS) as ColormapName[];

/** Map t in [0,1] to an RGB triple. */
export function colormap(name: ColormapName, t: number): Stop {
  const s = STOPS[name];
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (s.length - 1);
  const i = Math.min(s.length - 2, Math.floor(x));
  const f = x - i;
  const a = s[i], b = s[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

export function gradientCss(name: ColormapName, dir = "to right"): string {
  const n = 12;
  const stops = Array.from({ length: n }, (_, i) => {
    const [r, g, b] = colormap(name, i / (n - 1));
    return `rgb(${r | 0},${g | 0},${b | 0}) ${((i / (n - 1)) * 100).toFixed(0)}%`;
  });
  return `linear-gradient(${dir}, ${stops.join(",")})`;
}
