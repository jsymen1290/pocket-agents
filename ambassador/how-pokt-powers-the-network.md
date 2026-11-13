# Learn How POKT Powers the Network

Pocket Community Hub quest answer by jsymen.

1) Application stake: applications lock POKT, and settlement burns it as relays are proven. Stake is only an eligibility gate for suppliers, not a weight in selection.

2) Supplier rewards: after a session, a supplier submits a claim (Merkle root) and a proof. When the proof is valid, POKT minted for that settlement is split under mint=burn: 79% supplier, 14% block proposer (validators), 4.5% DAO, 2.5% source/service owner.

3) Service ownership: whoever registers a service sets its compute units per relay and gets the 2.5% owner share on every settlement, regardless of which supplier answered.

4) Supply policy: under PIP-41, for every 100 POKT burned by applications, 97.5 are minted back to participants and 2.5 are permanently removed.

Sources: https://docs.pocket.network/
