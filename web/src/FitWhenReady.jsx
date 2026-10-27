import { useEffect } from "react";
import { useReactFlow } from "@xyflow/react";

// Fits the view whenever `trigger` changes. The maps give every node an explicit width and height, so the
// bounds are known without waiting for measurement (a controlled flow without onNodesChange never gets
// measurements, so the `fitView` prop and useNodesInitialized() both silently do nothing). One animation
// frame lets the container take its final size first.
export function FitWhenReady({ padding = 0.08, trigger }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const frame = requestAnimationFrame(() => fitView({ padding, duration: 300 }));
    return () => cancelAnimationFrame(frame);
  }, [trigger, fitView, padding]);
  return null;
}
