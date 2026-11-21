import { readPreference, writePreference } from './preferences';

describe('preferences', () => {
  it('keeps each preference under the app’s own prefix', () => {
    writePreference('guide', 'hidden');
    writePreference('sidebar', 'collapsed');
    expect(Object.keys(window.localStorage).sort()).toEqual([
      'churnsense:guide',
      'churnsense:sidebar',
    ]);
    expect(readPreference('guide')).toBe('hidden');
    expect(readPreference('sidebar')).toBe('collapsed');
  });

  it('reads nothing that was never written', () => {
    expect(readPreference('guide')).toBeNull();
  });

  it('falls back quietly when the browser keeps no storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(() => {
      writePreference('guide', 'hidden');
    }).not.toThrow();
    expect(readPreference('guide')).toBeNull();
  });

  it('accepts only the preferences there are', () => {
    // @ts-expect-error: not a preference; a customer or a figure never is one.
    writePreference('customer', 'x');
    // @ts-expect-error: the design prototype's own key is not this app's.
    readPreference('cs_guide_hidden');
  });
});
