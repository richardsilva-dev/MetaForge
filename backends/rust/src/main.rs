use std::io::{self, Read};
use std::time::Instant;

fn heap_sort(values: &mut [i64]) {
    fn sift_down(values: &mut [i64], mut root: usize, end: usize) {
        loop {
            let left = root * 2 + 1;
            if left >= end {
                return;
            }
            let mut child = left;
            if child + 1 < end && values[child] < values[child + 1] {
                child += 1;
            }
            if values[root] >= values[child] {
                return;
            }
            values.swap(root, child);
            root = child;
        }
    }

    let len = values.len();
    for root in (0..len / 2).rev() {
        sift_down(values, root, len);
    }
    for end in (1..len).rev() {
        values.swap(0, end);
        sift_down(values, 0, end);
    }
}

fn shell_sort(values: &mut [i64]) {
    let mut gap = values.len() / 2;
    while gap > 0 {
        for index in gap..values.len() {
            let value = values[index];
            let mut cursor = index;
            while cursor >= gap && values[cursor - gap] > value {
                values[cursor] = values[cursor - gap];
                cursor -= gap;
            }
            values[cursor] = value;
        }
        gap /= 2;
    }
}

fn main() {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("input read failed");
        std::process::exit(2);
    }
    let mut tokens = input.split_whitespace();
    let strategy = tokens.next().unwrap_or("");
    let count: usize = match tokens.next().and_then(|value| value.parse().ok()) {
        Some(value) if value <= 100_000 => value,
        _ => {
            eprintln!("invalid input count");
            std::process::exit(2);
        }
    };
    let mut values = Vec::with_capacity(count);
    for _ in 0..count {
        match tokens.next().and_then(|value| value.parse::<i64>().ok()) {
            Some(value) => values.push(value),
            None => {
                eprintln!("invalid integer input");
                std::process::exit(2);
            }
        }
    }
    if tokens.next().is_some() {
        eprintln!("unexpected trailing input");
        std::process::exit(2);
    }

    let started = Instant::now();
    match strategy {
        "builtin" => values.sort_unstable(),
        "heapsort" => heap_sort(&mut values),
        "shellsort" => shell_sort(&mut values),
        _ => {
            eprintln!("strategy not supported by Rust backend");
            std::process::exit(2);
        }
    }
    let elapsed_ns = started.elapsed().as_nanos();
    println!("{elapsed_ns}");
    println!(
        "{}",
        values.iter().map(i64::to_string).collect::<Vec<_>>().join(" ")
    );
}
