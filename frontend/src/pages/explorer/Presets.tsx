/**
 * Saved views: a filter each, with how many customers it keeps (the API's
 * count). A view is pressed only while every filter is exactly what it sets.
 */
import { useCustomerCount } from '../../api/queries';
import type { SetParams } from '../../app/params';
import { ToggleChip } from '../../components/ui/Toggle';
import { int } from '../../lib/format';
import {
  explorerFilter,
  isPreset,
  presetChanges,
  presetFilters,
  PRESETS,
  type EXPLORER_PARAMS,
  type ExplorerState,
  type Preset,
} from './explorer';
import styles from './Explorer.module.css';

interface PresetsProps {
  state: ExplorerState;
  set: SetParams<typeof EXPLORER_PARAMS>;
}

export function Presets({ state, set }: PresetsProps) {
  return (
    <div role="group" aria-label="Saved views" className={styles.presets}>
      {PRESETS.map((preset) => (
        <PresetChip
          key={preset.label}
          preset={preset}
          pressed={isPreset(state, preset)}
          onApply={() => set(presetChanges(preset))}
        />
      ))}
    </div>
  );
}

function PresetChip({
  preset,
  pressed,
  onApply,
}: {
  preset: Preset;
  pressed: boolean;
  onApply: () => void;
}) {
  const count = useCustomerCount(explorerFilter(presetFilters(preset)));
  return (
    <ToggleChip
      variant="preset"
      pressed={pressed}
      onToggle={onApply}
      count={count.data !== undefined ? int(count.data) : count.isError ? '—' : '…'}
    >
      {preset.label}
    </ToggleChip>
  );
}
