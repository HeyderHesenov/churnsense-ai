/**
 * Whether a fader's thumb is drawn. The thumb is the native range's own
 * (styled in Fader.module.css), which no computed style reports, so the range
 * is photographed and its light pixels counted in the page: the thumb is the
 * only light thing on the track (the rail and ticks are dark), even dimmed, as
 * a fader that is off draws it (white at 0.55 over the dark ground is about 147).
 */
import type { Locator, Page } from '@playwright/test';

/** Light pixels in the range's picture, in its first ``columns`` columns. */
export async function thumbPixels(page: Page, range: Locator, columns = Infinity): Promise<number> {
  const png = await range.screenshot();
  return page.evaluate(
    async ([base64, width, LIGHT]) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext('2d');
      if (!context) throw new Error('no 2d context');
      context.drawImage(bitmap, 0, 0);
      const { data, width: across } = context.getImageData(0, 0, bitmap.width, bitmap.height);
      let light = 0;
      for (let i = 0; i < data.length; i += 4) {
        if ((i / 4) % across >= width) continue;
        const [r = 0, g = 0, b = 0] = [data[i], data[i + 1], data[i + 2]];
        if (r > LIGHT && g > LIGHT && b > LIGHT) light += 1;
      }
      return light;
    },
    [png.toString('base64'), columns, 110] as const,
  );
}
