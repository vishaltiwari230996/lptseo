/* Extraction stub — NOT the real ConsoleApp.
 *
 * `SeoAgent.tsx` and `labs.tsx` import one thing from this module: the `ToastFn`
 * type. The real `components/console/ConsoleApp.tsx` in the AgentOS frontend is
 * the whole multi-agent console shell and pulls in every other agent's panel,
 * so it is not part of a SEO-only extract. This file reproduces just the two
 * types, verbatim, which lets both SEO files stay byte-identical to their
 * originals.
 *
 * `app/page.tsx` here supplies the toast implementation the console shell used
 * to provide.
 */

export type ToastTone = "ok" | "warn" | "error";
/** Stable signature for every `onToast` prop in the console. */
export type ToastFn = (msg: string, tone?: ToastTone) => void;
