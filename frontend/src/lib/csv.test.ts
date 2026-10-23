import cases from '../../../tests/fixtures/csv_neutralisation_cases.json';
import { defuse, toCsv } from './csv';

describe('csv', () => {
  it.each(cases.cases)('defuses $input the way the server does', ({ input, expected }) => {
    expect(defuse(input)).toBe(expected);
  });

  it('quotes cells that hold separators and keeps numbers as numbers', () => {
    expect(
      toCsv(
        ['id', 'p'],
        [
          ['a,b', -0.5],
          ['=1', 0.25],
        ],
      ),
    ).toBe('id,p\n"a,b",-0.5\n\'=1,0.25\n');
  });

  it('defuses header names too', () => {
    expect(toCsv(['@x'], [])).toBe("'@x\n");
  });
});
