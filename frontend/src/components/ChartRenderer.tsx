// src/components/ChartRenderer.tsx
// Renders Plotly chart from fig.to_json() string sent by backend

import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import { useTheme } from "./ThemeProvider";

interface Props {
  chartJson: string;
}

export default function ChartRenderer({ chartJson }: Props) {
  const { theme } = useTheme();
  
  // Plotly canvas requires hex colors for grids/axes, so we resolve based on the active theme
  const isDark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const textColor = isDark ? "#f3f4f6" : "#111827";
  const gridColor = isDark ? "#374151" : "#e5e7eb";

  let data: Data[] = [];
  let layout: Partial<Layout> = {};

  try {
    const parsed = JSON.parse(chartJson);
    data = parsed.data ?? [];
    layout = { 
      ...parsed.layout, 
      autosize: true, 
      paper_bgcolor: 'transparent', 
      plot_bgcolor: 'transparent', 
      font: { color: textColor },
      xaxis: { ...parsed.layout.xaxis, gridcolor: gridColor, zerolinecolor: gridColor, linecolor: gridColor, tickfont: { color: textColor } },
      yaxis: { ...parsed.layout.yaxis, gridcolor: gridColor, zerolinecolor: gridColor, linecolor: gridColor, tickfont: { color: textColor } }
    };
  } catch {
    return <p style={{ color: "var(--warning-text)", fontSize: "13px" }}>Chart render error: invalid JSON</p>;
  }

  return (
    <Plot
      data={data}
      layout={layout}
      style={{ width: "100%", minHeight: 320 }}
      useResizeHandler
      config={{ responsive: true, displayModeBar: false }}
    />
  );
}
