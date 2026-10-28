import {
  contributionLabel,
  level,
  modelName,
  overviewLevel,
  profile,
  sentenceLabel,
} from './labels';

describe('labels', () => {
  it('writes a profile line the way the design does', () => {
    expect(profile('Month-to-month', 'Fiber optic', 'Electronic check')).toBe(
      'Month-to-month · Fiber · E-check',
    );
    expect(profile('Two year', 'No', 'Credit card (automatic)')).toBe(
      'Two year · No internet · Card',
    );
  });

  it('names breakdown levels per page', () => {
    expect(level('tenure', '0-3')).toBe('0–3');
    expect(level('monthly', '<$35')).toBe('< $35');
    expect(level('monthly', '$35-55')).toBe('$35–55');
    expect(level('InternetService', 'No')).toBe('None');
    expect(level('OnlineSecurity', 'No internet service')).toBe('No internet');
    expect(level('MultipleLines', 'No phone service')).toBe('No phone');
    expect(level('SeniorCitizen', '1')).toBe('Yes');
    expect(overviewLevel('monthly', '<$35')).toBe('Under $35');
    expect(overviewLevel('PaymentMethod', 'Bank transfer (automatic)')).toBe(
      'Bank transfer (auto)',
    );
    expect(overviewLevel('InternetService', 'No')).toBe('No internet');
  });

  it('shortens contribution and model names where the design does', () => {
    expect(contributionLabel('Tenure (months)')).toBe('Tenure');
    expect(contributionLabel('Tech support add-on')).toBe('Tech support');
    expect(contributionLabel('Monthly charges')).toBe('Monthly charges');
    expect(contributionLabel('Contract type')).toBe('Contract');
    expect(sentenceLabel('Tech support add-on')).toBe('Tech support add-on');
    expect(sentenceLabel('Total charges to date')).toBe('Total charges');
    expect(sentenceLabel('Monthly charges')).toBe('Monthly charges');
    expect(modelName('Hist Gradient Boosting')).toBe('Hist. gradient boosting');
  });
});
