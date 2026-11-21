/**
 * Which partition a card reads: validation or test, never training. The
 * choice is the page's (``?part=``), so the summary and the errors tab read
 * the same one, and Back undoes a change.
 */
import { Segmented } from '../../components/ui/Toggle';
import { PARTITION_NAMES } from './copy';
import { PARTITIONS, type Partition } from './tabs';

const OPTIONS = PARTITIONS.map((value) => ({ value, label: PARTITION_NAMES[value] }));

export function PartitionSwitch({
  value,
  onChange,
}: {
  value: Partition;
  onChange: (value: Partition) => void;
}) {
  return <Segmented label="Partition" options={OPTIONS} value={value} onChange={onChange} small />;
}
