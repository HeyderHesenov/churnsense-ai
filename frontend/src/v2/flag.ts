/**
 * The V2 Preview switch: off unless the build is made with
 * ``VITE_CHURNSENSE_V2_PREVIEW=1`` (``npm run dev`` for a local preview).
 *
 * Vite writes the value into the bundle at build time, so with the switch off
 * every branch it guards, and every V2 module only those branches load, is
 * left out of the build: no V2 page, link or request exists in a default (or
 * deployed) build. The one lazy import of the workspace (``app/App``) tests
 * ``import.meta.env`` itself rather than this constant: the bundler drops a
 * dynamic import only behind a condition it sees in the same module. The
 * built ``dist`` is checked for V2 code (``tests/test_workspace_workbench_api``).
 *
 * The API has its own switch as well (``CHURNSENSE_V2_DATA_HUB_PREVIEW=1``,
 * local deployments only); both must be on for the preview to work.
 */
export const V2_PREVIEW: boolean = import.meta.env.VITE_CHURNSENSE_V2_PREVIEW === '1';

/** The address every V2 page lives under. */
export const V2_ROOT = '/v2';

/** Whether ``pathname`` is a V2 page's address. */
export function isV2Path(pathname: string): boolean {
  return pathname === V2_ROOT || pathname.startsWith(`${V2_ROOT}/`);
}
