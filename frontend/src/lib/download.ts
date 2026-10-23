/** Hand the user a file built in the browser. Returns false if the browser refused. */
export function downloadText(
  filename: string,
  text: string,
  type = 'text/csv;charset=utf-8',
): boolean {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.rel = 'noopener';
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    return true;
  } catch {
    return false;
  }
}
