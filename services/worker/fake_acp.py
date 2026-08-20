"""Compatibilité de l'entrypoint de démonstration du worker."""

from services.fake_acp_agent.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
