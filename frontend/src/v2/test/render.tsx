/**
 * A V2 page as the reader gets it: the workspace (its frame, navigation and
 * lazily loaded page) at ``path``. Wait for the page with a ``findBy``.
 */
import { renderAt } from '../../test/render';
import Workspace from '../Workspace';

export function renderV2(path: string) {
  return renderAt(<Workspace />, path);
}
