# Interface assets

All interface assets are served locally. There are no runtime CDN, font-hosting, or external script requests.

## Silk Waves / Beautiful Backgrounds

- Source: https://github.com/Niekes/beautiful-backgrounds
- Pinned source commit: `a44535748fac1be7a29a714f38eca8faea880327`
- Selected component: `BbSilkWaves`, registered as `bb-silk-waves`.
- Copyright (c) 2026 Stefan Nieke, MIT license.
- Full notice: [beautiful-backgrounds-MIT.txt](web/vendor/licenses/beautiful-backgrounds-MIT.txt).
- The selected source and required helpers are retained in `web/background-src/`.
- Tracewatch modifies the shared animation base to cap drawing at 12 FPS and 1,280 canvas pixels in width, pause outside the viewport or during active analysis, and honor the operating system's reduced-motion preference. The reduced-motion view still draws a static background.
- The upstream Silk Waves implementation credits the Waves effect from Vanta by Teng Bao: https://github.com/tengbao/vanta/blob/master/src/vanta.waves.js.

## Lit

The self-contained background bundle includes Lit 3.3.3 and its supporting packages. These use the BSD 3-Clause license; full notices are in `web/vendor/licenses/`. Exact installed versions are pinned in `package-lock.json`; `web/vendor/versions.json` records the bundled component and build-tool versions.

## Open Sans

The variable Open Sans font is bundled at `web/fonts/open-sans-variable.ttf` under the SIL Open Font License 1.1. Full copyright and license terms are included in [OFL.txt](web/fonts/OFL.txt).

Source: https://github.com/google/fonts/tree/main/ofl/opensans

## Rebuilding the background

The generated bundle is committed; Node is not needed to run the application. To rebuild after changing the selected source:

```text
npm ci
npm run build:background
```

The build copies the relevant dependency license notices next to the generated JavaScript. The Python app continues to serve the committed assets directly.
