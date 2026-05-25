# JoinQuant Qixing Daily ETF Rotation

Daily-bar implementation of the JoinQuant community "Qixing Gaozhao 4.0" ETF rotation idea.

The strategy ranks ETF/LOF candidates by 24-day weighted log-price regression annualized return multiplied by regression R-squared. It keeps the highest positive-scoring candidate when enough liquid candidates pass the volume filter, otherwise it switches to `511880` as the defensive leg.

Risk controls are intentionally simple to match the public 4.0 description: a recent 3-day drawdown stop and a fixed stop from the filled entry price. The public source discloses the structure but not full executable code or exact thresholds, so threshold defaults are conservative implementation assumptions rather than a claim of byte-for-byte JoinQuant parity.

Source:
- https://www.joinquant.com/community/post/detailMobile?postId=67252
