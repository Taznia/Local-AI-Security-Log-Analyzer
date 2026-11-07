import "./components/silkWaves";
import type { BbSilkWaves } from "./components/silkWaves";

// A committed, self-contained browser bundle is served by FastAPI. No CDN.
const background = document.querySelector<BbSilkWaves>("bb-silk-waves");
if (background) {
    background.paused = document.documentElement.dataset.analysisActive === "true";
    window.addEventListener("tracewatch:analysis-state", (event: Event) => {
        background.paused = Boolean((event as CustomEvent<{ running: boolean }>).detail.running);
    });
}
