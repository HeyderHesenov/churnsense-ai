import {
  contributionLabel,
  FEATURE,
  FEATURES,
  level,
  modelName,
  overviewLevel,
  profile,
  sentenceLabel,
  valueText,
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

  it('writes a feature value as the API sends it, a number or a word', () => {
    expect(valueText('tenure', 65)).toBe('65 months');
    expect(valueText('tenure', 1)).toBe('1 month');
    expect(valueText('MonthlyCharges', 90)).toBe('$90.00');
    expect(valueText('TotalCharges', 1810.25)).toBe('$1,810.25');
    expect(valueText('SeniorCitizen', '0')).toBe('No');
    expect(valueText('SeniorCitizen', 1)).toBe('Yes');
    expect(valueText('Contract', 'Two year')).toBe('Two year');
    expect(valueText('PaymentMethod', 'Credit card (automatic)')).toBe('Credit card (auto)');
  });

  it('lists every feature once, in the column contract’s order', () => {
    expect(FEATURES).toEqual([
      'SeniorCitizen',
      'Partner',
      'Dependents',
      'tenure',
      'PhoneService',
      'MultipleLines',
      'InternetService',
      'OnlineSecurity',
      'OnlineBackup',
      'DeviceProtection',
      'TechSupport',
      'StreamingTV',
      'StreamingMovies',
      'Contract',
      'PaperlessBilling',
      'PaymentMethod',
      'MonthlyCharges',
      'TotalCharges',
    ]);
    expect([...FEATURES].sort()).toEqual(Object.keys(FEATURE).sort());
  });
});
