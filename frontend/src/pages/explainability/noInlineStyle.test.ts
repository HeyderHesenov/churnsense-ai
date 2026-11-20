/**
 * The explainability page sets no style attribute: dynamic geometry is SVG attributes and
 * fixed places are classes, so nothing on the page needs the CSP's style
 * exceptions. Read from the source, so a style prop added anywhere fails here.
 * Its reach is this folder: shared components (src/components) are not read,
 * and some of them still set CSS variables through a style prop.
 */
const sources = import.meta.glob<string>(['./**/*.tsx', './**/*.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

describe('the explainability page’s source', () => {
  it('has components to check', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(8);
  });

  it.each(Object.entries(sources).filter(([path]) => !path.includes('.test.')))(
    '%s sets no style attribute and writes no HTML',
    (_path, source) => {
      expect(source).not.toMatch(/\bstyle=/);
      expect(source).not.toMatch(/\bstyle\s*:|\.style\b|setAttribute\(\s*['"]style/);
      expect(source).not.toMatch(/\bcssVars\b/);
      expect(source).not.toMatch(/dangerouslySetInnerHTML|innerHTML|\beval\(/);
    },
  );
});
