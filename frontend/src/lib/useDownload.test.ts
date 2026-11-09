import { act, renderHook } from '@testing-library/react';
import { downloadText } from './download';
import { useDownload } from './useDownload';

vi.mock('./download', () => ({ downloadText: vi.fn(() => true) }));

describe('useDownload', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(downloadText).mockReset().mockReturnValue(true);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const refusing = (about = 'a') => {
    vi.mocked(downloadText).mockReturnValue(false);
    return renderHook((props: { about: string }) => useDownload(props.about), {
      initialProps: { about },
    });
  };

  it('saves the file, and says nothing when the browser takes it', () => {
    const { result } = renderHook(() => useDownload('a'));
    act(() => {
      result.current.save('x.csv', 'text');
    });
    expect(downloadText).toHaveBeenCalledWith('x.csv', 'text');
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.unsaved).toBe(false);
  });

  it('says a refused save a moment later, emptied first so a second one is heard too', () => {
    const { result } = refusing();
    act(() => {
      result.current.save('x.csv', 'text');
    });
    expect(result.current.unsaved).toBe(false);
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.unsaved).toBe(true);
    act(() => {
      result.current.save('x.csv', 'text');
    });
    expect(result.current.unsaved).toBe(false);
    act(() => {
      vi.advanceTimersByTime(99);
    });
    expect(result.current.unsaved).toBe(false);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.unsaved).toBe(true);
  });

  it('forgets a refusal once it is about something else, for good, or when asked', () => {
    const { result, rerender } = refusing();
    act(() => {
      result.current.save('x.csv', 'text');
    });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    rerender({ about: 'b' });
    expect(result.current.unsaved).toBe(false);
    rerender({ about: 'a' });
    expect(result.current.unsaved).toBe(false);
    act(() => {
      result.current.save('x.csv', 'text');
    });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    act(() => {
      result.current.forget();
    });
    expect(result.current.unsaved).toBe(false);
  });

  it('says nothing of a refusal once it is forgotten or followed by a save, before it is said', () => {
    const { result } = refusing();
    act(() => {
      result.current.save('x.csv', 'text');
    });
    act(() => {
      vi.advanceTimersByTime(50);
      result.current.forget();
      vi.advanceTimersByTime(100);
    });
    expect(result.current.unsaved).toBe(false);
    act(() => {
      result.current.save('x.csv', 'text');
    });
    vi.mocked(downloadText).mockReturnValue(true);
    act(() => {
      vi.advanceTimersByTime(50);
      result.current.save('x.csv', 'text');
      vi.advanceTimersByTime(100);
    });
    expect(result.current.unsaved).toBe(false);
  });

  it('never says a refusal about what has gone in the meantime', () => {
    const { result, rerender } = refusing();
    act(() => {
      result.current.save('x.csv', 'text');
    });
    rerender({ about: 'b' });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.unsaved).toBe(false);
  });

  it('stops its timer when it is unmounted', () => {
    const { result, unmount } = refusing();
    act(() => {
      result.current.save('x.csv', 'text');
    });
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
