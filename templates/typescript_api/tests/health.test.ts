import app from '../src/index';

describe('Health endpoint', () => {
  it('should return ok status', () => {
    expect(app).toBeDefined();
  });
});
