Keys: 1,000,000. Trials per row: 20 (different shard names).
Ideal: 50 % per shard with 2 shards; 33.3 % of keys moved when adding a 3rd.

| hash | vnodes/shard | balance: mean deviation from 50 % | balance: worst | moved on 2→3: mean | moved: worst deviation from 33.3 % |
|---|---|---|---|---|---|
| md5 | 1 | 27.7 pp | 45.5 pp | 44.6 % | 46.8 pp |
| md5 | 10 | 10.3 pp | 31.3 pp | 33.2 % | 15.0 pp |
| md5 | 50 | 3.8 pp | 13.9 pp | 34.0 % | 8.3 pp |
| md5 | 100 | 3.4 pp | 7.7 pp | 34.2 % | 5.6 pp |
| md5 | 200 | 1.7 pp | 5.6 pp | 34.1 % | 7.2 pp |
| md5 | 500 | 1.1 pp | 3.4 pp | 33.7 % | 3.1 pp |
| sha256 | 1 | 27.0 pp | 48.0 pp | 37.7 % | 56.9 pp |
| sha256 | 10 | 8.1 pp | 16.5 pp | 33.7 % | 16.0 pp |
| sha256 | 50 | 4.2 pp | 10.5 pp | 33.1 % | 8.8 pp |
| sha256 | 100 | 2.3 pp | 7.0 pp | 33.4 % | 7.7 pp |
| sha256 | 200 | 1.7 pp | 4.5 pp | 33.4 % | 5.1 pp |
| sha256 | 500 | 1.2 pp | 2.9 pp | 33.7 % | 4.3 pp |
| blake2b | 1 | 29.8 pp | 46.6 pp | 40.9 % | 50.4 pp |
| blake2b | 10 | 8.7 pp | 23.3 pp | 33.8 % | 15.4 pp |
| blake2b | 50 | 5.2 pp | 12.7 pp | 32.8 % | 5.9 pp |
| blake2b | 100 | 2.1 pp | 5.5 pp | 33.0 % | 6.0 pp |
| blake2b | 200 | 1.7 pp | 6.7 pp | 33.3 % | 5.4 pp |
| blake2b | 500 | 1.5 pp | 3.9 pp | 33.8 % | 3.2 pp |

Without a ring (md5 % N): moved on 2→3 = 66.6 % of keys.

Built-in hash() of the same key in 3 separate Python processes:
  hash('usertable#user42') = 1787881812548007799
  hash('usertable#user42') = 1863101163256402828
  hash('usertable#user42') = -5726314691521540453
