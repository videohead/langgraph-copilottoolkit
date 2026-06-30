from src.basic_graph.graph import graph


if __name__ == "__main__":
    result = graph.invoke(
        {"user_input": "hello"},
        {"configurable": {"thread_id": "cli"}},
    )
    print(result)
