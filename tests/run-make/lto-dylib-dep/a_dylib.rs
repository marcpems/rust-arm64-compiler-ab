pub fn foo() {
    println!("bar");
}

pub static VALUE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(41);
