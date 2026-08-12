"""GridCast — a self-grading grid carbon forecasting service.

This package is deliberately empty of imports. The serving API imports
``gridcast.config`` and ``gridcast.db``, and must never transitively pull in
httpx, pandas or a modelling stack (SRS NFR-6). Re-exporting anything here
would defeat that.
"""

__version__ = "0.1.0"
