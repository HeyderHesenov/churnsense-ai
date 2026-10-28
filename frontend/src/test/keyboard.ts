import { act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/** Press a control from the keyboard, as a reader without a pointer would: the focus starts on it. */
export async function press(control: HTMLElement): Promise<void> {
  act(() => control.focus());
  await userEvent.keyboard('{Enter}');
}
