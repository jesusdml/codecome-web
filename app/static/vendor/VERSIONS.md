# Vendored front-end library

| File | Source | SHA-256 |
|---|---|---|
| `preact-htm.js` | https://cdn.jsdelivr.net/npm/htm@3.1.1/preact/standalone.module.js (htm 3.1.1 with its bundled Preact 10 and hooks) | `72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7` |

- Downloaded 2026-11-24 with user approval (design doc section 11, item 6). Unmodified.
- Self-contained ES module: no imports, no network access. Only URL inside is the SVG namespace string.
- Exports: `h, html, render, Component, createContext, useState, useReducer, useEffect, useLayoutEffect, useRef,
  useImperativeHandle, useMemo, useCallback, useContext, useDebugValue, useErrorBoundary`.
- To verify: `sha256sum app/static/vendor/preact-htm.js`. To upgrade, change the version in the URL, re-hash, and update this file.
- Licenses (downloaded 2026-11-27 with user approval, unmodified):
  - htm: Apache-2.0, Copyright 2018 Google Inc. — `LICENSE-htm.txt` from https://raw.githubusercontent.com/developit/htm/3.1.1/LICENSE
    (`740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f`)
  - Preact (bundled by htm's standalone build): MIT, Copyright (c) 2015-present Jason Miller — `LICENSE-preact.txt`
    from https://raw.githubusercontent.com/preactjs/preact/main/LICENSE (`1fe6958409c8c257a70c587a18b6f7f412b179b456630790d30b2ec9a8e4b7d4`)
  - Both are compatible with this project's GPL-3.0-or-later OR AGPL-3.0-or-later. See NOTICE at the repository root.

## Fonts (SIL Open Font License 1.1, licence texts next to the files)

Source: npm @fontsource-variable/inter@5.2.8 and @fontsource-variable/jetbrains-mono@5.2.8 (jsDelivr), latin + latin-ext variable (wght) subsets, downloaded 2026-11-25.

```
34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956  inter-latin-ext.woff2
3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62  inter-latin.woff2
79bfdab9ba467e26eea4122e6f2567e188dd8a09a8c730d501fc487c4ab99c6e  jetbrains-mono-latin-ext.woff2
18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e  jetbrains-mono-latin.woff2
```
