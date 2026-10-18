package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.domain.report.Report;
import com.sense2act.backend.domain.report.ReportMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 事件图谱查询(E5-3,契约 §2 事件图谱组)。
 * graph 端点:节点 = 事件本身 + 全部参与实体(event_entities);
 * 边 = event_entities(事件→主体,relation 即角色)+ 触及节点集的 event_relations
 * (关系另一端的外部主体也并入节点集,一跳扩展)。
 */
@Service
public class GraphQueryService {

    private static final int MAX_PAGE_SIZE = 100;

    private final GraphEventMapper eventMapper;
    private final GraphEntityMapper entityMapper;
    private final EventEntityLinkMapper entityLinkMapper;
    private final EventRelationMapper relationMapper;
    private final ReportMapper reportMapper;

    public GraphQueryService(GraphEventMapper eventMapper, GraphEntityMapper entityMapper,
                             EventEntityLinkMapper entityLinkMapper, EventRelationMapper relationMapper,
                             ReportMapper reportMapper) {
        this.eventMapper = eventMapper;
        this.entityMapper = entityMapper;
        this.entityLinkMapper = entityLinkMapper;
        this.relationMapper = relationMapper;
        this.reportMapper = reportMapper;
    }

    public Map<String, Object> list(Integer page, Integer pageSize) {
        int p = page == null || page < 1 ? 1 : page;
        int size = pageSize == null || pageSize < 1 ? 20 : Math.min(pageSize, MAX_PAGE_SIZE);
        Long total = eventMapper.selectCount(null);
        List<Map<String, Object>> items = eventMapper.selectList(new LambdaQueryWrapper<GraphEvent>()
                        .orderByDesc(GraphEvent::getCreatedAt).orderByDesc(GraphEvent::getId)
                        .last("LIMIT " + size + " OFFSET " + (p - 1) * size))
                .stream().map(GraphQueryService::listItem).toList();
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("total", total);
        data.put("page", p);
        data.put("page_size", size);
        data.put("items", items);
        return data;
    }

    public Map<String, Object> detail(String id) {
        GraphEvent event = requireEvent(id);
        Map<String, Object> data = eventView(event);
        data.put("entities", entitiesWithRoles(id));
        return data;
    }

    public Map<String, Object> graph(String id) {
        GraphEvent event = requireEvent(id);
        List<Map<String, Object>> entities = entitiesWithRoles(id);
        Set<String> entityIds = new LinkedHashSet<>();
        for (Map<String, Object> e : entities) {
            entityIds.add((String) e.get("id"));
        }

        List<Map<String, Object>> nodes = new ArrayList<>();
        nodes.add(node("event", event.getId(), event.getTitle(), event.getType()));
        for (Map<String, Object> e : entities) {
            nodes.add(node("entity", (String) e.get("id"), (String) e.get("name"), (String) e.get("type")));
        }

        List<Map<String, Object>> edges = new ArrayList<>();
        for (Map<String, Object> e : entities) {
            edges.add(edge(event.getId(), "event", (String) e.get("id"), "entity", (String) e.get("role")));
        }
        // 触及本事件或其主体的关系;另一端是外部主体时并入节点(一跳扩展)
        for (EventRelation r : relationMapper.selectList(new LambdaQueryWrapper<EventRelation>()
                .orderByAsc(EventRelation::getId))) {
            boolean sourceIn = inSet(r.getSourceType(), r.getSourceId(), event.getId(), entityIds);
            boolean targetIn = inSet(r.getTargetType(), r.getTargetId(), event.getId(), entityIds);
            if (!sourceIn && !targetIn) {
                continue;
            }
            edges.add(edge(r.getSourceId(), r.getSourceType(), r.getTargetId(), r.getTargetType(),
                    r.getRelation()));
            addExternalNode(nodes, r.getSourceType(), r.getSourceId(), event.getId(), entityIds);
            addExternalNode(nodes, r.getTargetType(), r.getTargetId(), event.getId(), entityIds);
        }

        List<Map<String, Object>> reports = reportMapper.selectList(new LambdaQueryWrapper<Report>()
                        .eq(Report::getEventId, id).orderByAsc(Report::getCreatedAt))
                .stream().map(r -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", r.getId());
                    m.put("title", r.getTitle());
                    m.put("status", r.getStatus());
                    return m;
                }).toList();

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("event", eventView(event));
        data.put("nodes", nodes);
        data.put("edges", edges);
        data.put("reports", reports);
        return data;
    }

    private GraphEvent requireEvent(String id) {
        GraphEvent event = eventMapper.selectById(id);
        if (event == null) {
            throw BusinessException.notFound("事件不存在: " + id);
        }
        return event;
    }

    private List<Map<String, Object>> entitiesWithRoles(String eventId) {
        List<EventEntityLink> links = entityLinkMapper.selectList(
                new LambdaQueryWrapper<EventEntityLink>().eq(EventEntityLink::getEventId, eventId));
        if (links.isEmpty()) {
            return List.of();
        }
        List<String> ids = links.stream().map(EventEntityLink::getEntityId).toList();
        Map<String, GraphEntity> byId = new LinkedHashMap<>();
        for (GraphEntity ge : entityMapper.selectBatchIds(ids)) {
            byId.put(ge.getId(), ge);
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (EventEntityLink link : links) {
            GraphEntity ge = byId.get(link.getEntityId());
            if (ge == null) {
                continue;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", ge.getId());
            m.put("name", ge.getName());
            m.put("type", ge.getType());
            m.put("role", link.getRole());
            out.add(m);
        }
        return out;
    }

    private static boolean inSet(String nodeType, String nodeId, String eventId, Set<String> entityIds) {
        if ("event".equals(nodeType)) {
            return eventId.equals(nodeId);
        }
        return "entity".equals(nodeType) && entityIds.contains(nodeId);
    }

    private void addExternalNode(List<Map<String, Object>> nodes, String nodeType, String nodeId,
                                 String eventId, Set<String> entityIds) {
        if (inSet(nodeType, nodeId, eventId, entityIds)) {
            return;
        }
        GraphEntity ge = "entity".equals(nodeType) ? entityMapper.selectById(nodeId) : null;
        if (ge != null) {
            nodes.add(node("entity", ge.getId(), ge.getName(), ge.getType()));
            entityIds.add(ge.getId());   // 同一外部主体只补一次
        }
    }

    private static Map<String, Object> listItem(GraphEvent e) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", e.getId());
        m.put("title", e.getTitle());
        m.put("type", e.getType());
        m.put("status", e.getStatus());
        m.put("start_date", e.getStartDate() == null ? null : e.getStartDate().toString());
        m.put("created_at", e.getCreatedAt());
        return m;
    }

    private static Map<String, Object> eventView(GraphEvent e) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", e.getId());
        m.put("title", e.getTitle());
        m.put("type", e.getType());
        m.put("summary", e.getSummary());
        m.put("status", e.getStatus());
        m.put("start_date", e.getStartDate() == null ? null : e.getStartDate().toString());
        m.put("created_at", e.getCreatedAt());
        return m;
    }

    private static Map<String, Object> node(String nodeType, String id, String label, String type) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("node_type", nodeType);
        m.put("id", id);
        m.put("label", label);
        m.put("type", type);
        return m;
    }

    private static Map<String, Object> edge(String sourceId, String sourceType, String targetId,
                                            String targetType, String relation) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("source", sourceId);
        m.put("source_type", sourceType);
        m.put("target", targetId);
        m.put("target_type", targetType);
        m.put("relation", relation);
        return m;
    }
}
