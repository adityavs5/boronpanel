# BoronPanel 1.1.3

Includes Evolution Icons Grid, Paper Lantern and the tested self-update fixes from 1.1.0–1.1.2.

Fixes the Updates page returning an error after a successful self-update: SQLite ORM timestamps and finalizer timestamps can differ in timezone representation. Job-duration reporting now normalizes both to UTC and supports existing completed jobs. Regression tests cover all timestamp combinations and reporting after a real finalizer run.
