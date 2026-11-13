from newstar_sdk.parsers import (
    parse_building_queue,
    parse_research_queue,
    parse_ship_queue,
)


def test_building_queue(fixture_html):
    queue = parse_building_queue(fixture_html("buildings_queue.html"))
    assert len(queue) == 1
    entry = queue[0]
    assert entry.index == 0
    assert entry.element_id == 31
    assert entry.name == "科研实验室"
    assert entry.target_level == 1
    assert entry.remaining_seconds == 3
    assert entry.active is True
    assert entry.end_time


def test_empty_building_queue(fixture_html):
    assert parse_building_queue(fixture_html("buildings.html")) == []


def test_research_queue(fixture_html):
    queue = parse_research_queue(fixture_html("research_queue.html"))
    assert len(queue) == 1
    entry = queue[0]
    assert entry.element_id == 108
    assert entry.name == "计算机技术"
    assert entry.target_level == 1
    assert entry.remaining_seconds == 51
    assert entry.active is True


def test_ship_queue():
    html = """
    <div id="buildlist">
      <div class="element_row active_row" style="background:url(./styles/theme/nsc/gebaeude/202.gif)">
        <div class="right_hand">
          <span class="onlistretit">轻型运输舰</span>
          <div id="time" data-time="120"></div>
          <span class="onlistremov"><span id="auftr0">10</span></span>
          <span class="onlistrenum">1</span>
        </div>
      </div>
      <div class="element_row" style="background:url(./styles/theme/nsc/gebaeude/203.gif)">
        <div class="right_hand">
          <span class="onlistrenum">2</span>
          <span class="onlistremov">5</span>
        </div>
      </div>
    </div>
    """
    queue = parse_ship_queue(html)
    assert len(queue) == 2
    assert queue[0].index == 0
    assert queue[0].element_id == 202
    assert queue[0].amount == 10
    assert queue[0].remaining_seconds == 120
    assert queue[1].index == 1
    assert queue[1].element_id == 203
    assert queue[1].name == "重型运输舰"
    assert queue[1].amount == 5
