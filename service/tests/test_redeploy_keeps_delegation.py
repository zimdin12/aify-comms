"""An update does not move managed spawns off aify-env.

`install.sh` bakes delegation only when asked, and `redeploy.sh` re-renders every launcher by calling
it. So a routine update -- whose entire promise is that it changes nothing but the code -- silently
moved spawns back to being hosted by the bridge. Observed on the release host minutes after the flip:
`aify-comms doctor`'s `spawn-delegation` went from `delegated` to `local` across one redeploy.

This is the THIRD setting an update has quietly discarded, after the endpoint (which would have
repointed a whole fleet at loopback) and the notification hook. Each was fixed the same way and for
the same reason: read back what the host already chose, from the file, because asking a launcher by
running it starts an environment bridge and supersedes the live one.

Absence stays absence. The reader prints nothing when delegation is off, so a caller cannot confuse a
recovered setting with an invented one -- and inventing this one would point spawns at a daemon nobody
chose.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
READER = ROOT / "scripts" / "installed-delegation.sh"
REDEPLOY = ROOT / "redeploy.sh"


def _bash() -> str:
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def read(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash(), READER.as_posix(), directory.as_posix()],
        capture_output=True, text=True, timeout=120,
    )


def launcher(directory: Path, *, on: bool, endpoint: str = "http://127.0.0.1:8802") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "aify-comms").write_text(
        "#!/usr/bin/env bash\n"
        f'export AIFY_COMMS_DELEGATE_SPAWNS="{"1" if on else ""}"\n'
        f'export AIFY_ENV_ENDPOINT="{endpoint if on else ""}"\n',
        encoding="utf-8",
    )


def test_a_delegated_launcher_reports_its_endpoint(tmp_path):
    launcher(tmp_path, on=True, endpoint="http://10.0.0.4:8802")
    result = read(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://10.0.0.4:8802"


def test_delegation_off_prints_nothing_and_fails(tmp_path):
    # An un-delegated install must be reproduced exactly, which means passing no flag at all.
    launcher(tmp_path, on=False)
    result = read(tmp_path)
    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_a_missing_launcher_is_absence_not_a_default(tmp_path):
    result = read(tmp_path)
    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_a_pre_contract_launcher_reports_nothing(tmp_path):
    # Every launcher rendered before the setting existed. Recovering a default here would turn
    # delegation ON for a host that never asked for it.
    (tmp_path / "aify-comms").write_text("#!/usr/bin/env bash\nexec node bridge.js\n", encoding="utf-8")
    assert read(tmp_path).returncode == 1


def test_delegation_on_with_no_endpoint_is_refused(tmp_path):
    # install.sh would default the endpoint, and defaulting silently is what this reader prevents.
    (tmp_path / "aify-comms").write_text(
        '#!/usr/bin/env bash\nexport AIFY_COMMS_DELEGATE_SPAWNS="1"\nexport AIFY_ENV_ENDPOINT=""\n',
        encoding="utf-8",
    )
    assert read(tmp_path).returncode == 1


def test_whitespace_is_not_a_setting(tmp_path):
    (tmp_path / "aify-comms").write_text(
        '#!/usr/bin/env bash\nexport AIFY_COMMS_DELEGATE_SPAWNS="   "\n'
        'export AIFY_ENV_ENDPOINT="http://x:8802"\n',
        encoding="utf-8",
    )
    assert read(tmp_path).returncode == 1


#: Every spelling that means "off". The reader accepted all of these as ON while `env-client.mjs` --
#: the code that actually decides whether a spawn is delegated -- refused to delegate for every one,
#: so `redeploy.sh` would have carried forward a setting that was never in effect.
NEGATIVE_SPELLINGS = ("0", "false", "no", "off", "maybe")


@pytest.mark.parametrize("value", NEGATIVE_SPELLINGS)
def test_a_value_that_means_off_is_off(tmp_path, value):
    (tmp_path / "aify-comms").write_text(
        f'#!/usr/bin/env bash{chr(10)}export AIFY_COMMS_DELEGATE_SPAWNS="{value}"{chr(10)}'
        f'export AIFY_ENV_ENDPOINT="http://x:8802"{chr(10)}',
        encoding="utf-8",
    )
    result = read(tmp_path)
    assert result.returncode == 1, f'"{value}" was read as delegation ON'
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("value", ("1", "true", "yes", "on", "ON", " on "))
def test_a_value_that_means_on_is_on(tmp_path, value):
    """The control. A reader that refused everything would pass every case above and be useless."""
    (tmp_path / "aify-comms").write_text(
        f'#!/usr/bin/env bash{chr(10)}export AIFY_COMMS_DELEGATE_SPAWNS="{value}"{chr(10)}'
        f'export AIFY_ENV_ENDPOINT="http://x:8802"{chr(10)}',
        encoding="utf-8",
    )
    result = read(tmp_path)
    assert result.returncode == 0, f'"{value}" was read as delegation OFF: {result.stderr}'
    assert result.stdout.strip() == "http://x:8802"


def test_THE_FOURTH_READER_IS_GONE_RATHER_THAN_AGREEING():
    """RETIRED SUBJECT, kept as an assertion because "it left" and "it drifted" look identical.

    There used to be a fourth reader of this setting and it was the one the operator actually saw:
    the launcher's own start-up banner. It printed "spawns: DELEGATED to aify-env" whenever the
    value was non-blank, so a host with `="0"` was told on every single start that its spawns went
    somewhere they did not. A banner is not a code path, which is exactly why it drifted -- nothing
    executed it.

    v0.6.1 removed the start-up entirely: `aify-comms` starts no environment bridge, so there is no
    banner to keep in step. Three readers remain and each is exercised above by running it. What
    this pins is that the fourth did not come back in some other spelling, because a reader added
    beside a value rather than beside its parser is how the original one drifted.
    """
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "spawns: DELEGATED to aify-env" not in install, (
        "the launcher announces delegation again; it needs the four-word parser, not a truthiness "
        "test, or it will report DELEGATED for a value that disables it"
    )
    # POSITIVE CONTROL: the setting is still WRITTEN, so its absence above is the banner going and
    # not the whole feature being renamed out from under this assertion.
    assert re.search(r'export AIFY_COMMS_DELEGATE_SPAWNS="', install), (
        "the installer no longer records where spawns run; installed-delegation.sh reads this line"
    )


def test_redeploy_passes_the_recovered_setting_to_install():
    """The reader is only half of it: redeploy has to hand what it found back to install.sh."""
    text = REDEPLOY.read_text(encoding="utf-8")
    assert "installed-delegation.sh" in text, "redeploy does not read the setting back"
    assert "--delegate-spawns" in text, "redeploy reads the setting and never passes it on"
    # Passed as an ARRAY that expands to nothing when empty, so an un-delegated install is reproduced
    # exactly rather than being handed an empty flag.
    assert "DELEGATE_ARGS" in text
