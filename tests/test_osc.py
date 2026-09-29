import math

from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind
from stagewatch.integrations.osc_out import OscOutIntegration


def test_osc_messages_include_site_env_and_alarm(tmp_path):
    hub = Hub(tmp_path)
    hub.config.site.smoothing_tau_s = 0
    hub.register_device(Device("n", "n", "test"))
    hub.register_entity(Entity("n.temperature", "n", "t", Kind.TEMPERATURE))
    hub.update_state("n.temperature", 20.0)
    hub.compute_site()
    hub.config.osc_out.per_node = True
    msgs = dict(OscOutIntegration(hub).build_messages())
    env = msgs["/stagewatch/avg/env"]
    assert env[0] == 20.0 and math.isnan(env[1]) and math.isnan(env[2]) and env[4] == 1
    assert 340 < env[3] < 346
    assert msgs["/stagewatch/alarm"] == [0, 0]
    assert msgs["/stagewatch/node/n/env"][0] == 20.0
    hub.recorder.close()
