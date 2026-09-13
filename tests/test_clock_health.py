import pytest
from daemon import clock_health as clock


def tracking(**overrides):
    values=['A29FC801','162.159.200.1','4','1000','0.0001','0','0','0','0','0','0.1','0.002','256','Normal']
    for key,value in overrides.items():values[int(key)]=str(value)
    return ','.join(values)


def test_healthy_clock_uses_offset_and_uncertainty():
    result=clock.parse_tracking(tracking(),now=1100)
    assert result['status']=='healthy'
    assert result['error_bound_seconds']==pytest.approx(.0521)


@pytest.mark.parametrize('changes,status',[
    ({'4':'2'},'warning'),({'4':'-6'},'critical'),({'11':'7'},'critical'),
    ({'13':'Not synchronised'},'critical'),({'0':'7F7F0101'},'critical'),
    ({'3':'-1000'},'critical'),({'3':'2000'},'critical'),({'2':'0'},'critical'),
])
def test_drift_unsynchronized_and_stale_clocks(changes,status):
    assert clock.parse_tracking(tracking(**changes),now=1100)['status']==status


@pytest.mark.parametrize('value',['broken',tracking(**{'4':'nan'}),tracking(**{'10':'-1'})])
def test_bad_tracking_is_rejected(value):
    with pytest.raises(ValueError):clock.parse_tracking(value,now=1100)


def test_failed_probe_is_unknown_and_cached(monkeypatch):
    monkeypatch.setattr(clock,'_cache',None)
    calls=[]
    def fail(*args,**kwargs):calls.append(args);raise OSError('chrony unavailable')
    monkeypatch.setattr(clock,'run',fail)
    assert clock.get_status()['status']=='unknown'
    assert clock.get_status()['status']=='unknown'
    assert len(calls)==1
